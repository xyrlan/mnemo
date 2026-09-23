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
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
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
#: Written by the session-start block, not by a command. Named like
#: ``inbox-offers.jsonl``'s and for the same reason: without it, "this was
#: put in front of someone" is not a fact, and :func:`stats` cannot say
#: whether the offer is what gets a candidate decided.
OFFERED = "offered"

#: What the session-start block reads instead of scanning. The scan costs
#: ~1.0 s over the 184 transcripts on disk (2026-09-19); the hook may not
#: pay that, so a detached refresh writes this and the hook reads it.
CACHE_REL = ".mnemo/procedure-candidates.json"
#: Touched *before* the refresh is spawned, so a scan that dies does not
#: get retried on every session start for ever.
SCAN_MARKER_REL = ".mnemo/procedure-scan.last"

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
#: ``mnemo-desktop`` lives in ``mnemo-desktop-wt-round6-wt-104``. A twin
#: (#449) is ``mnemo-wt-449-3fa9c1``.
_FOLD = re.compile(r"-wt-(\d+-[0-9a-f]{6}|\d+|c-[a-z0-9-]+|[a-z0-9]+)$")
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


def record(
    vault_root: Path,
    *,
    event: str,
    repo: str,
    key: str,
    session_id: Optional[str] = None,
) -> None:
    """Append one row. Never raises — a ledger row is not worth a command.

    Three events land here, and only two are decisions: ``accepted`` and
    ``dropped`` are written by ``mnemo procedures``, ``offered`` by the
    session-start block. ``session_id`` is carried for the offer, so a row can
    be traced back to the session that was shown it.

    ``newline=""``: no CRLF translation on Windows. The rotation cap is a byte
    budget, and a row costing one more byte per line there would rotate the
    same ledger at a different record.
    """
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
        if session_id:
            row["session_id"] = session_id
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


# --------------------------------------------------------------------------
# the offer: a candidate reaches the maintainer without being asked for (#397)
# --------------------------------------------------------------------------
#
# The command and the ``doctor`` row are both pulls, and #385 priced a pull at
# 5 of 182 children. #380 built the push for the *other* queue — a bounded
# block on the session-start prompt — and this is the same push for this one,
# with one difference that decides the whole shape: a staged page is a file
# already on disk, and a candidate does not exist until 184 transcripts have
# been read. That read costs ~1.0 s, which the hook may not pay. So the scan
# runs detached, writes :data:`CACHE_REL`, and the hook reads that.
#
# What the hook then owes the reader is honesty about staleness. The cache may
# be up to ``refreshIntervalHours`` old, and in that window two things could
# have made a cached candidate wrong: the maintainer decided it (the ledger
# says so, and the ledger is read live), or they wrote the line into
# ``CLAUDE.md`` by hand (:func:`states` is re-run live against the file, which
# is one small read and only when there is something to offer). Anything else
# a stale cache gets wrong — a candidate that crossed the bar an hour ago — is
# a candidate offered a day late, which is the cost this design chose.


def offer_settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The offer's bounds, resolved from config with the documented defaults.

    Same four names as ``inbox``'s, plus the one this queue needs and that one
    does not: ``refreshIntervalHours``, how stale the scan behind the offer may
    be. ``offerOnSessionStart: false`` silences the block and leaves ``mnemo
    procedures`` working — including ``--refresh``, so the cache a user wants
    for ``doctor`` is still theirs to rebuild.
    """
    raw = (cfg or {}).get("procedures") or {}

    def _int(key: str, default: int) -> int:
        try:
            return int(raw.get(key, default))
        except (TypeError, ValueError):
            return default

    return {
        "enabled": bool(raw.get("offerOnSessionStart", True)),
        "max": max(0, _int("offerMax", 1)),
        "cooldown_days": max(0, _int("offerCooldownDays", 7)),
        "interval_hours": max(0, _int("offerIntervalHours", 24)),
        "refresh_hours": max(0, _int("refreshIntervalHours", 24)),
    }


# --- the cache -------------------------------------------------------------


def cache_path(vault_root: Path) -> Path:
    return Path(vault_root) / CACHE_REL


def _carrier_row(carrier: Carrier) -> Dict[str, Any]:
    return {
        "name": carrier.name,
        "values": [[value, count] for value, count in carrier.values],
        "rediscovered": list(carrier.rediscovered),
        "kept": carrier.kept,
        "never": carrier.never,
        "stated": carrier.stated,
    }


def _carrier_from_row(row: Dict[str, Any]) -> Carrier:
    values = tuple(
        (str(pair[0]), int(pair[1]))
        for pair in (row.get("values") or [])
        if isinstance(pair, (list, tuple)) and len(pair) == 2
    )
    return Carrier(
        name=str(row["name"]),
        values=values,
        rediscovered=tuple(str(x) for x in (row.get("rediscovered") or [])),
        kept=int(row.get("kept") or 0),
        never=int(row.get("never") or 0),
        stated=bool(row.get("stated")),
    )


def candidate_row(candidate: Candidate) -> Dict[str, Any]:
    """One candidate as JSON. The round trip is exact, so the block and the
    command are reading the same object and cannot disagree about it."""
    return {
        "repo": candidate.repo,
        "shape": candidate.shape,
        "carriers": [_carrier_row(c) for c in candidate.carriers],
        "shape_children": candidate.shape_children,
        "first_day": candidate.first_day,
        "last_day": candidate.last_day,
        "repo_root": candidate.repo_root,
    }


def candidate_from_row(row: Dict[str, Any]) -> Optional[Candidate]:
    """Rebuild one candidate, or ``None`` when the row is not one.

    A cache written by an older version, or half-written, must cost the
    session nothing — so a row that does not parse is dropped, not raised on.
    """
    try:
        return Candidate(
            repo=str(row["repo"]),
            shape=str(row["shape"]),
            carriers=tuple(_carrier_from_row(c) for c in row["carriers"]),
            shape_children=int(row.get("shape_children") or 0),
            first_day=str(row.get("first_day") or ""),
            last_day=str(row.get("last_day") or ""),
            repo_root=row.get("repo_root") or None,
        )
    except Exception:  # noqa: BLE001
        return None


def write_cache(
    vault_root: Path,
    candidates: Sequence[Candidate],
    *,
    projects: str = "",
    now: Optional[datetime] = None,
) -> bool:
    """Write what the session-start block will read. Never raises.

    Written whole through a temp file: the reader is a hook, and a half-written
    JSON file read by one would cost a session its offer for a day.
    """
    try:
        path = cache_path(vault_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": (now or datetime.now()).isoformat(timespec="seconds"),
            "projects": projects,
            "candidates": [candidate_row(c) for c in candidates],
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception:  # noqa: BLE001
        return False


def read_cache(vault_root: Path) -> Tuple[List[Candidate], Optional[datetime]]:
    """``(candidates, when they were scanned)``. ``([], None)`` when there is
    no cache, which is what every vault looks like until the first refresh
    lands. Never raises."""
    try:
        raw = json.loads(cache_path(vault_root).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return [], None
    if not isinstance(raw, dict):
        return [], None
    out: List[Candidate] = []
    for row in raw.get("candidates") or []:
        if isinstance(row, dict):
            candidate = candidate_from_row(row)
            if candidate is not None:
                out.append(candidate)
    stamp = None
    try:
        stamp = datetime.fromisoformat(str(raw.get("generated_at")))
    except ValueError:
        stamp = None
    return out, stamp


# --- when the scan runs ----------------------------------------------------


def scan_marker_path(vault_root: Path) -> Path:
    return Path(vault_root) / SCAN_MARKER_REL


def scan_is_due(vault_root: Path, cfg: Dict[str, Any], *, now: Optional[datetime] = None) -> bool:
    """Is the cache behind the offer older than its refresh interval?

    Read off the *marker*, not the cache, and the difference is the one #234
    and #229 both paid for: a scan that dies — no transcripts, a permissions
    error, a machine without ``~/.claude/projects`` — writes no cache, and a
    staleness check against the cache would then spawn a fresh scan on every
    single session start for ever. The marker is stamped before the spawn, so
    a failure costs one interval, not an unbounded loop.
    """
    settings = offer_settings(cfg)
    if not settings["enabled"] or settings["max"] == 0:
        return False
    if settings["refresh_hours"] == 0:
        return False
    try:
        stamp = scan_marker_path(vault_root).stat().st_mtime
    except OSError:
        return True
    ref = (now or datetime.now()).timestamp()
    return ref - stamp >= settings["refresh_hours"] * 3600


def mark_scan(vault_root: Path) -> None:
    """Stamp the marker. Never raises."""
    try:
        path = scan_marker_path(vault_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    except Exception:  # noqa: BLE001
        return None


# --- what to offer ---------------------------------------------------------


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def last_block_at(rows: Iterable[Dict[str, Any]], repo: str) -> Optional[datetime]:
    """When this repo was last shown a block, from its newest ``offered`` row."""
    newest: Optional[datetime] = None
    for row in rows:
        if row.get("event") != OFFERED or str(row.get("repo") or "") != repo:
            continue
        stamp = _parse_ts(row.get("ts"))
        if stamp is not None and (newest is None or stamp > newest):
            newest = stamp
    return newest


def last_offered(rows: Iterable[Dict[str, Any]]) -> Dict[str, datetime]:
    """Newest ``offered`` timestamp per ``repo:key``."""
    out: Dict[str, datetime] = {}
    for row in rows:
        if row.get("event") != OFFERED:
            continue
        stamp = _parse_ts(row.get("ts"))
        if stamp is None:
            continue
        key = f"{row.get('repo', '')}:{row.get('key')}"
        if key not in out or stamp > out[key]:
            out[key] = stamp
    return out


def restate(candidate: Candidate) -> Candidate:
    """The same candidate, with ``stated`` re-read from the repo's file now.

    The cache is up to a day old and ``CLAUDE.md`` is the maintainer's to edit
    by hand at any moment. Offering a line the file already carries is the one
    staleness that would look like the tool not reading, so it is the one
    bought back — for the price of reading one small file, and only when there
    is something to offer.
    """
    text = read_claude_md(candidate.repo_root)
    carriers = tuple(
        replace(carrier, stated=states(text, candidate.shape, carrier.name))
        for carrier in candidate.carriers
    )
    return replace(candidate, carriers=carriers)


def pick_offers(
    vault_root: Path,
    repo: str,
    *,
    cfg: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Tuple[List[Candidate], int]:
    """``(candidates to offer, undecided candidates for this repo)``. Never raises.

    The bounds are checked cheapest first, and the order is load-bearing — this
    runs on every session start, while the block it feeds is emitted at most
    once a day per repo. The interval gate is a ledger read of a few kilobytes;
    everything after it touches the cache and then the repo's ``CLAUDE.md``. A
    silenced call therefore reports ``0`` waiting, the same lie-free shortcut
    ``inbox.pick_offers`` takes: nobody reads a total that comes with no
    candidates.
    """
    try:
        settings = offer_settings(cfg)
        if not settings["enabled"] or settings["max"] == 0 or not repo:
            return [], 0
        ref = now or datetime.now()
        rows = ledger_rows(Path(vault_root))
        interval = settings["interval_hours"]
        if interval:
            previous = last_block_at(rows, repo)
            if previous is not None and ref - previous < timedelta(hours=interval):
                return [], 0
        cached, _stamp = read_cache(Path(vault_root))
        decided = _decided(rows)
        mine = [
            c for c in cached
            if c.repo == repo and f"{c.repo}:{c.key}" not in decided
        ]
        if not mine:
            return [], 0
        waiting = [c for c in (restate(c) for c in mine) if not c.stated]
        if not waiting:
            return [], 0
        cooldown = timedelta(days=settings["cooldown_days"])
        offered = last_offered(rows)
        fresh = [
            c for c in waiting
            if settings["cooldown_days"] == 0
            or f"{c.repo}:{c.key}" not in offered
            or ref - offered[f"{c.repo}:{c.key}"] >= cooldown
        ]
        return fresh[: settings["max"]], len(waiting)
    except Exception:  # noqa: BLE001 — runs inside the session-start hook
        return [], 0


# --------------------------------------------------------------------------
# the numbers
# --------------------------------------------------------------------------


def stats(
    vault_root: Path,
    candidates: Sequence[Candidate],
    *,
    window_days: int = 7,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """What the queue is doing: depth, and what the offer drained.

    ``candidates`` is passed in rather than scanned for, because the caller
    already has them and a second scan would cost another second and could
    disagree with the first.

    ``median_decision_days`` is measured from a candidate's *first* offer to
    the decision that closed it, and is None until one candidate has been
    through both. That is the number that answers the question this offer was
    built to answer — whether being shown a candidate is what gets it decided,
    rather than someone sitting down with ``mnemo procedures`` again.
    """
    ref = now or datetime.now()
    since = ref - timedelta(days=window_days)
    first_offer: Dict[str, datetime] = {}
    latencies: List[float] = []
    offered = accepted = dropped = 0
    for row in ledger_rows(Path(vault_root)):
        stamp = _parse_ts(row.get("ts"))
        event = row.get("event")
        key = f"{row.get('repo', '')}:{row.get('key')}"
        if stamp is None or not row.get("key"):
            continue
        if event == OFFERED and key not in first_offer:
            first_offer[key] = stamp
        if event in (ACCEPTED, DROPPED) and key in first_offer:
            latencies.append((stamp - first_offer[key]).total_seconds() / 86400)
        if stamp < since:
            continue
        if event == OFFERED:
            offered += 1
        elif event == ACCEPTED:
            accepted += 1
        elif event == DROPPED:
            dropped += 1

    # The inbox's median, not a second one: two review queues that report the
    # same kind of number differently are two queues nobody compares. Imported
    # here rather than at module scope because this is the only line of this
    # module that knows the other queue exists.
    from mnemo.core.inbox import median

    unstated = [c for c in candidates if not c.stated]
    return {
        "window_days": window_days,
        "candidates": len(unstated),
        "repos": len({c.repo for c in unstated}),
        "offered": offered,
        "accepted": accepted,
        "dropped": dropped,
        "resolved": accepted + dropped,
        "median_decision_days": median(latencies),
    }
