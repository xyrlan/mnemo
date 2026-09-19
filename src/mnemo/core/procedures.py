"""Procedures children keep rediscovering, and the `CLAUDE.md` line that would stop it (#392).

#385 measured the channel: Claude Code attaches a repo's ``CLAUDE.md`` to every
dispatched child, lean profile included, whether or not the issue mentions
testing — where a ranked rule arrives only when the prompt's words match. Then
it wrote this repo's file by hand. This module is the missing half: it reads
the transcripts on disk, finds what children of a repo *worked out for
themselves more than once*, and proposes the line that would have told them.

Nothing here edits a ``CLAUDE.md``. :func:`accept` appends one section when a
maintainer names one candidate, and that is the only write.

**Why this is not ``mnemo inbox``** (#380). That queue and this one share
their discipline — one decision per item, a ledger so a refusal is a knowable
fact, nothing promoted on its own — and nothing else. A staged page is a
vault file whose promotion is a ``mv`` inside the vault; a procedure is a
proposal about a *repo*, its acceptance writes a file mnemo does not own, and
the object does not exist until the transcripts are read. Sharing
``shared/_inbox/`` would have meant a ``--promote`` that means two different
things depending on the key it is given. What they should share, and do, is
the maintainer's attention: the ``doctor`` row is this queue's offer, priced
at nothing, where the inbox's rides a session-start budget already spent.

**Detection carries no probes.** ``tools/measure_child_procedures.py`` (#385)
asks "did this child run ``pytest`` with ``PYTHONPATH``" — a question someone
already knew the answer to, so it can only confirm, never find. Here the
question is asked backwards, of the transcript alone:

1. A **shape** is a command as far as its first subcommand — ``cargo test``,
   ``pnpm tauri dev``, ``python3 pytest``. A **carrier** is an environment
   assignment written in front of it (``PYTHONPATH=src pytest``), or exported
   earlier in the same command.
2. Per child and shape, a carrier is **rediscovered** when the child ran that
   shape without it and later with it, **kept** when every run carried it, and
   **never** when no run did.
3. A shape that children of more than ``maxShapeRepos`` repos run is the
   harness's, not this repo's — ``git log``, ``gh issue``, ``grep``. Measured
   over the 184 dispatch transcripts on disk on 2026-09-19, that one rule is
   what separates ``python3 pytest`` (one repo) from ``python3`` on its own
   (four), and it is derived from the population, not written down per repo.

**What this cannot see**, measured on the same 184 (``tools/
measure_rediscovered_procedures.py --rejected``):

- *A procedure that lives in a flag.* clubinho's ``--runInBand`` and
  ``--max-old-space-size`` are boundaries; ``cargo test --nocapture`` and
  ``npx eslint --fix`` are a task's own choice. The same bar over flags
  qualifies 11 more lines, led by ``--nocapture`` and ``--ignored`` at 12
  children each and ``claude --help`` at 6 — in a transcript, a flag a child
  adopted and a flag a child chose are the same event. Flags are counted and
  excluded rather than guessed at, so the exclusion is a number and not an
  opinion.
- *A procedure nobody ever finds.* Rediscovery needs the second half: a child
  that never gets it right leaves no adoption to observe. That is the
  expensive state #385 named, and it stays invisible here.
- *Failure.* Of the 247 runs of those shapes that were missing something the
  repo needs, 6 failed in a way the child could see. The runs that matter
  succeed — a bare ``pytest`` in a mnemo worktree prints ``83 passed`` against
  the main checkout — which is why this had to be measured and could not be
  noticed.
"""
from __future__ import annotations

import glob
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from mnemo.core.log_utils import iter_rotated_rows, rotate_if_needed

#: Append-only record of what a maintainer did with a candidate, under the
#: vault's ``.mnemo/`` — beside ``inbox-offers.jsonl`` and for the same reason
#: (#380): without it, "this was already refused" is not a knowable fact and
#: the same line is proposed for ever.
LEDGER_REL = ".mnemo/procedure-decisions.jsonl"
MAX_BYTES = 1_048_576

ACCEPTED = "accepted"
DROPPED = "dropped"

#: Statement separators. A pipe counts: ``cargo test | tail`` runs one command.
_STATEMENT = re.compile(r"(?:&&|\|\||;|\n|\|)")
_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_PROGRAM = re.compile(r"^[A-Za-z][A-Za-z0-9._+-]*$")
_FLAG = re.compile(r"^--[A-Za-z][A-Za-z0-9-]*$")
_SUBCOMMAND = re.compile(r"^[A-Za-z][A-Za-z0-9:._-]*$")
#: ``pnpm run test`` and ``npm exec jest`` name the work one token further on.
_PASSTHROUGH = ("run", "exec", "x", "tauri")
_RUNNERS = ("npm", "pnpm", "yarn", "bun", "npx", "uv", "poetry")
#: Dispatch worktree suffixes, folded repeatedly: a child of a child of
#: ``mnemo-desktop`` lives in ``mnemo-desktop-wt-round6-wt-104``.
_FOLD = re.compile(r"-wt-(\d+|c-[a-z0-9-]+|[a-z0-9]+)$")
#: Repos the live suite builds in a temp dir: ``live``, ``live-model``, ``live-full``.
_LIVE_TREE = re.compile(r"^live(-|$)")


@dataclass(frozen=True)
class Carrier:
    """One environment assignment children of a repo work out for themselves."""

    name: str
    values: Tuple[Tuple[str, int], ...]
    rediscovered: Tuple[str, ...]
    kept: int
    never: int
    stated: bool

    @property
    def value(self) -> str:
        """The value most runs gave it, or ``""`` when none was captured."""
        return self.values[0][0] if self.values else ""

    @property
    def varies(self) -> bool:
        """Did children give it more than one value?

        ``CARGO_TARGET_DIR`` is per worktree by construction, so the commonest
        value is a shape to copy, not a value to paste. A proposal that hid
        that would be a line the maintainer had to catch.
        """
        return len(self.values) > 1


@dataclass(frozen=True)
class Candidate:
    """One proposed ``CLAUDE.md`` line: a shape, and what it has to carry here.

    The unit is the *line*, not the carrier, because that is what a maintainer
    accepts and what the repo pays for. ``pnpm test`` needing both
    ``DEVELOPER_DIR`` and ``PATH`` is one thing to say, not two.

    ``rediscovered`` holds worktree names, not a count, so a reader can go and
    read one of the children that paid.
    """

    repo: str
    shape: str
    carriers: Tuple[Carrier, ...]
    shape_children: int
    first_day: str
    last_day: str
    repo_root: Optional[str] = None

    @property
    def key(self) -> str:
        """``cargo-test`` — typeable, and stable across runs."""
        return self.shape.replace(" ", "-")

    @property
    def rediscovered(self) -> Tuple[str, ...]:
        """Every child that worked out any of this line's carriers."""
        return tuple(sorted({wt for c in self.carriers for wt in c.rediscovered}))

    @property
    def unstated(self) -> Tuple[Carrier, ...]:
        """The carriers the repo's ``CLAUDE.md`` does not already name."""
        return tuple(c for c in self.carriers if not c.stated)

    @property
    def stated(self) -> bool:
        return not self.unstated

    @property
    def command(self) -> str:
        """The shape with its carriers in front, as a child would have to type it."""
        prefix = " ".join(f"{c.name}={c.value or '…'}" for c in self.carriers)
        return f"{prefix} {self.shape}".strip()


@dataclass(frozen=True)
class Result:
    ok: bool
    message: str


# --------------------------------------------------------------------------
# reading a transcript
# --------------------------------------------------------------------------

def fold_repo(name: str) -> str:
    """The repo a worktree name was cut from, folded until it stops shrinking."""
    previous = None
    while previous != name:
        previous, name = name, _FOLD.sub("", name)
    return name


def _records(path: str) -> Iterator[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def _blocks(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    content = (record.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _cwd_of(path: str) -> Optional[str]:
    for index, record in enumerate(_records(path)):
        if index > 200:
            return None
        if isinstance(record.get("cwd"), str):
            return record["cwd"]
    return None


def dispatch_transcripts(projects: str) -> List[Tuple[str, str]]:
    """``(transcript, cwd)`` for every child that ran in a dispatch worktree.

    The same population ``tools/measure_child_procedures.py`` reads: named by
    ``dispatch.issue_for_cwd``, the live suite's own trees excluded (see
    :func:`is_test_tree`), so a child whose worktree ``claude rm`` already
    pruned still counts — 184 of them on 2026-09-19.
    """
    from mnemo.core.dispatch import issue_for_cwd

    out: List[Tuple[str, str]] = []
    for directory in sorted(glob.glob(os.path.join(projects, "*"))):
        for path in sorted(glob.glob(os.path.join(directory, "*.jsonl"))):
            cwd = _cwd_of(path)
            if cwd and issue_for_cwd(cwd) is not None and not is_test_tree(cwd):
                out.append((path, cwd))
    return out


def is_test_tree(cwd: str) -> bool:
    """A worktree pytest made, not a child that did work.

    The live ``claude --bg`` probes build their repos as ``live-model``,
    ``live-full`` and the like under a pytest temp dir, then dispatch into
    ``…-wt-N``; those parse as dispatch worktrees and ran no one's work.
    Both halves are required — ``tools/measure_exploration.py`` keys on the
    temp dir alone, which would also swallow this module's own tests, whose
    synthetic repos live under ``tmp_path`` like every other test's. Measured
    on 2026-09-19: the temp dir alone drops 194 transcripts to 184,
    and all 10 it drops are ``live``, ``live-model`` or ``live-full``.
    """
    posix = cwd.replace("\\", "/")
    repo = fold_repo(os.path.basename(posix.rstrip("/")))
    return "/pytest-of-" in posix and _LIVE_TREE.match(repo) is not None


def invocations(command: str) -> Iterator[Tuple[str, Dict[str, str], Tuple[str, ...]]]:
    """``(shape, {env: value}, (flag, …))`` for each statement *command* runs.

    Heredoc bodies are dropped and quoted spans blanked before splitting, so a
    test file being written that quotes ``cargo test`` is not a run of it.
    An ``export`` applies to the statements after it in the same command and
    no further — a shell's own scope, and the widest one a transcript proves.

    Flags come back beside the environment because the module's central claim
    is about them: only the environment is acted on, and
    ``tools/measure_rediscovered_procedures.py --rejected`` counts what
    treating flags the same way would have proposed.
    """
    from mnemo.core.activity.exploration import _mask_quotes, _split_heredocs

    body, _ = _split_heredocs(command)
    masked = _mask_quotes(body)
    statements: List[str] = []
    start = 0
    for separator in _STATEMENT.finditer(masked):
        statements.append(body[start:separator.start()])
        start = separator.end()
    statements.append(body[start:])

    exported: Dict[str, str] = {}
    for raw in statements:
        words = raw.split()
        if not words:
            continue
        if words[0] == "export":
            for word in words[1:]:
                match = _ASSIGNMENT.match(word)
                if match:
                    exported[match.group(1)] = match.group(2)
            continue
        carriers = dict(exported)
        index = 0
        while index < len(words):
            match = _ASSIGNMENT.match(words[index])
            if not match:
                break
            carriers[match.group(1)] = match.group(2)
            index += 1
        if index >= len(words):
            continue
        program = os.path.basename(words[index].strip("'\""))
        if not _PROGRAM.match(program):
            continue
        shape = [program]
        rest = words[index + 1:]
        position = 0
        while position < len(rest):
            word = rest[position]
            if word == "-m" and position + 1 < len(rest):
                shape.append(rest[position + 1])
                position += 2
                continue
            if word.startswith("-") or len(shape) >= 3 or not _SUBCOMMAND.match(word):
                break
            shape.append(word)
            position += 1
            if program in _RUNNERS and shape[-1] in _PASSTHROUGH:
                continue
            break
        flags = tuple(sorted({
            word.split("=")[0] for word in rest
            if word.startswith("--") and _FLAG.match(word.split("=")[0])
        }))
        yield " ".join(shape), carriers, flags


#: The two kinds of carrier :func:`scan` can be asked about. ``ENV`` is what
#: the command proposes from; ``FLAG`` exists so the exclusion can be counted.
ENV = "env"
FLAG = "flag"


def _runs_by_shape(path: str, kind: str = ENV) -> Dict[str, List[Dict[str, str]]]:
    """Every shape this child ran, in order, with the carriers each run had."""
    runs: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for record in _records(path):
        for block in _blocks(record):
            if block.get("type") != "tool_use" or block.get("name") != "Bash":
                continue
            command = (block.get("input") or {}).get("command")
            if not isinstance(command, str):
                continue
            for shape, env, flags in invocations(command):
                runs[shape].append(env if kind == ENV else {flag: "" for flag in flags})
    return runs


def _first_day(path: str) -> str:
    for index, record in enumerate(_records(path)):
        if index > 200:
            break
        stamp = record.get("timestamp")
        if isinstance(stamp, str):
            return stamp[:10]
    return ""


def verdicts(runs: Sequence[Dict[str, str]]) -> Dict[str, str]:
    """``{carrier: "rediscovered" | "kept" | "never"}`` over one shape's runs.

    Rediscovered is the middle state and the only one this module acts on: the
    child ran the shape without the carrier, then with it. The knowledge was
    reachable and it paid to reach it.
    """
    out: Dict[str, str] = {}
    for carrier in {name for run in runs for name in run}:
        with_it = next((i for i, run in enumerate(runs) if carrier in run), None)
        without = next((i for i, run in enumerate(runs) if carrier not in run), None)
        if without is None:
            out[carrier] = "kept"
        elif with_it is not None and without < with_it:
            out[carrier] = "rediscovered"
        else:
            out[carrier] = "never"
    return out


# --------------------------------------------------------------------------
# the scan
# --------------------------------------------------------------------------

@dataclass
class _Tally:
    rediscovered: List[str]
    kept: int
    never: int
    values: "Counter"
    days: List[str]


def scan(
    projects: str,
    *,
    min_children: int = 2,
    max_shape_repos: int = 2,
    repo: Optional[str] = None,
    kind: str = ENV,
    ledger_rows: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[Candidate]:
    """Every candidate the transcripts under *projects* support, best first.

    *repo* narrows the result, never the population: shape ubiquity is a fact
    about every repo's children, so a scan of one repo alone would call ``git
    log`` that repo's own procedure.
    """
    children: List[Tuple[str, str, str, str, Dict[str, List[Dict[str, str]]]]] = []
    for path, cwd in dispatch_transcripts(projects):
        name = os.path.basename(cwd.rstrip("/"))
        children.append((fold_repo(name), name, cwd,
                         _first_day(path), _runs_by_shape(path, kind)))

    shape_repos: Dict[str, Set[str]] = defaultdict(set)
    shape_children: Dict[Tuple[str, str], int] = Counter()
    for repo_name, _wt, _cwd, _day, runs in children:
        for shape in runs:
            shape_repos[shape].add(repo_name)
            shape_children[(repo_name, shape)] += 1

    roots: Dict[str, str] = {}
    tallies: Dict[Tuple[str, str, str], _Tally] = {}
    for repo_name, worktree, cwd, day, runs in children:
        roots.setdefault(repo_name, os.path.join(os.path.dirname(cwd.rstrip("/")), repo_name))
        for shape, shape_runs in runs.items():
            if len(shape_repos[shape]) > max_shape_repos:
                continue
            for carrier, verdict in verdicts(shape_runs).items():
                tally = tallies.setdefault(
                    (repo_name, shape, carrier),
                    _Tally(rediscovered=[], kept=0, never=0, values=Counter(), days=[]),
                )
                if verdict == "rediscovered":
                    tally.rediscovered.append(worktree)
                elif verdict == "kept":
                    tally.kept += 1
                else:
                    tally.never += 1
                for run in shape_runs:
                    value = run.get(carrier)
                    if value:
                        tally.values[value] += 1
                if verdict == "rediscovered":
                    tally.days.append(day)

    decided = _decided(ledger_rows or [])
    files = {name: read_claude_md(root) for name, root in roots.items()}
    by_shape: Dict[Tuple[str, str], List[Carrier]] = defaultdict(list)
    for (repo_name, shape, carrier), tally in tallies.items():
        if len(tally.rediscovered) < min_children:
            continue
        stated = states(files.get(repo_name, ""), shape, carrier)
        by_shape[(repo_name, shape)].append(Carrier(
            name=carrier,
            values=tuple(tally.values.most_common(3)),
            rediscovered=tuple(sorted(tally.rediscovered)),
            kept=tally.kept,
            never=tally.never,
            stated=stated,
        ))

    out: List[Candidate] = []
    for (repo_name, shape), carriers in by_shape.items():
        if repo is not None and repo_name != repo:
            continue
        if f"{repo_name}:{shape.replace(' ', '-')}" in decided:
            continue
        days = sorted(d for name in (c.name for c in carriers)
                      for d in tallies[(repo_name, shape, name)].days if d)
        root = roots.get(repo_name)
        out.append(Candidate(
            repo=repo_name,
            shape=shape,
            carriers=tuple(sorted(carriers, key=lambda c: (-len(c.rediscovered), c.name))),
            shape_children=shape_children[(repo_name, shape)],
            first_day=days[0] if days else "",
            last_day=days[-1] if days else "",
            repo_root=root if root and os.path.isdir(root) else None,
        ))
    out.sort(key=lambda c: (c.repo, -len(c.rediscovered), c.shape))
    return out


# --------------------------------------------------------------------------
# what the repo already says
# --------------------------------------------------------------------------

def read_claude_md(repo_root: Optional[str]) -> str:
    """The repo's own ``CLAUDE.md``, or ``""`` when it has none."""
    if not repo_root:
        return ""
    try:
        return Path(repo_root, "CLAUDE.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def states(text: str, shape: str, carrier: str) -> bool:
    """Does *text* already tell a child about this carrier?

    The carrier's name, as a word. Not the shape as well: mnemo's file says
    ``PYTHONPATH=src python3 -m pytest -q`` and a child reading it is told,
    whether or not the spelling matches the one measured here. A false
    positive costs a proposal that would have been redundant; a false negative
    costs the maintainer being asked to write a line their file already has.
    """
    if not text:
        return False
    # Not ``\b``: a flag starts with ``-``, which is not a word character, so
    # ``\b--runInBand`` never matches after a space. The boundary that holds
    # for both spellings is "no word character and no dash either side".
    pattern = r"(?<![\w-])" + re.escape(carrier) + r"(?![\w-])"
    return re.search(pattern, text) is not None


# --------------------------------------------------------------------------
# the proposed line
# --------------------------------------------------------------------------

def proposed_section(candidate: Candidate, *, today: Optional[str] = None) -> str:
    """The ``CLAUDE.md`` section this candidate proposes — heading and body.

    Seven lines, on purpose: every session in that repo pays for every line,
    and the *reason* a maintainer would give is theirs to write, not this
    module's to guess. What it can say is what was measured, so an accepted
    line ships with the thing that measured it — this repo's own standing rule.

    Only carriers the file does not already name are written, so accepting a
    line for ``pnpm test`` after ``PATH`` was already stated adds
    ``DEVELOPER_DIR`` and does not repeat the other.
    """
    stamp = today or datetime.now().strftime("%Y-%m-%d")
    carriers = candidate.unstated or candidate.carriers
    names = ", ".join(f"`{c.name}`" for c in carriers)
    prefix = " ".join(f"{c.name}={c.value or '…'}" for c in carriers)
    n = len({wt for c in carriers for wt in c.rediscovered})
    child_word = "child" if n == 1 else "children"
    spread = [c for c in carriers if c.varies]
    varied = (" Children gave " + ", ".join(f"`{c.name}` {len(c.values)} values" for c in spread)
              + " — the commonest is above.") if spread else ""
    return "\n".join([
        f"## Run `{candidate.shape}` with {names}",
        "",
        "```sh",
        f"{prefix} {candidate.shape}".strip(),
        "```",
        "",
        f"{n} dispatched {child_word} of the {candidate.shape_children} that ran "
        f"`{candidate.shape}` ran it without this first and added it after "
        f"({stamp}, `mnemo procedures`). Say here why it is needed."
        + varied,
    ]) + "\n"


# --------------------------------------------------------------------------
# the two decisions
# --------------------------------------------------------------------------

def ledger_path(vault_root: Path) -> Path:
    return Path(vault_root) / LEDGER_REL


def ledger_rows(vault_root: Path) -> List[Dict[str, Any]]:
    try:
        return [row for row in iter_rotated_rows(ledger_path(vault_root))
                if row.get("key") and row.get("event")]
    except Exception:  # noqa: BLE001 — a listing is better than a traceback
        return []


def _decided(rows: Iterable[Dict[str, Any]]) -> Set[str]:
    """``repo:key`` for every candidate already accepted or dropped.

    Both decisions silence it. An accepted line lands in ``CLAUDE.md`` and
    :func:`states` would catch it anyway; a dropped one would otherwise come
    back on the next scan, which is the whole failure #380 named.
    """
    return {f"{row.get('repo', '')}:{row.get('key')}" for row in rows
            if row.get("event") in (ACCEPTED, DROPPED)}


def record(vault_root: Path, *, event: str, repo: str, key: str) -> None:
    """Append one decision. Never raises — a ledger row is not worth a command."""
    try:
        path = ledger_path(vault_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        rotate_if_needed(path, MAX_BYTES)
        row = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            "repo": repo,
            "key": key,
        }
        with open(path, "a", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
    except Exception:  # noqa: BLE001
        return None


def accept(vault_root: Optional[Path], candidate: Candidate, *, today: Optional[str] = None) -> Result:
    """Append this candidate's section to the repo's ``CLAUDE.md``.

    The only write in this module, and it only ever adds: the file is read,
    the section appended after its last byte, and nothing already there is
    rewritten — the maintainer's own text cannot be clobbered by an append.
    A line whose every carrier the file already names is refused rather than
    given a second opinion on the same subject.
    """
    if not candidate.repo_root or not os.path.isdir(candidate.repo_root):
        return Result(False, f"no checkout for {candidate.repo} at "
                             f"{candidate.repo_root or '?'} — nothing to write {candidate.key} into")
    path = Path(candidate.repo_root) / "CLAUDE.md"
    existing = read_claude_md(candidate.repo_root)
    if candidate.stated:
        names = ", ".join(f"`{c.name}`" for c in candidate.carriers)
        return Result(False, f"{path} already says {names} — nothing written")

    section = proposed_section(candidate, today=today)
    if existing:
        joiner = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        text = existing + joiner + section
    else:
        text = "# Working in this repo\n\nHow work is run here.\n\n" + section
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as error:
        return Result(False, f"could not write {path}: {error}")
    if vault_root is not None:
        record(Path(vault_root), event=ACCEPTED, repo=candidate.repo, key=candidate.key)
    lines = len(text.splitlines())
    return Result(True, f"appended to {path} — {lines} lines now, and every "
                        f"session in {candidate.repo} reads all of them")


def drop(vault_root: Path, candidate: Candidate) -> Result:
    """Take a candidate out of the queue without writing to the repo."""
    record(Path(vault_root), event=DROPPED, repo=candidate.repo, key=candidate.key)
    return Result(True, f"dropped {candidate.key} — it will not be proposed for "
                        f"{candidate.repo} again")
