"""One issue, two blind children: the pilot #439 asked for (#449).

#439 sized a child-level vault A/B at 85 to 580 pairs, and said which two
unknowns decide where in that range it lands: how far apart two children
given the *same* issue come out (run-to-run variance), and how often a reader
cannot say which of two diffs they would merge (the tie rate). Nothing had
ever been run twice, so neither was known. This module is what runs an issue
twice and records what the maintainer thought of the two results.

**Indistinguishable to themselves.** Both twins get the same opening prompt
byte for byte — :func:`mnemo.core.dispatch.build_prompt` with ``blind=True``,
which leaves the branch unnamed because each twin needs a branch of its own —
branched from the same commit, resolved once before either tree exists, with
the same model, effort and profile. Nothing tells either one it has a twin.
The prompt's sha256 is recorded with the pair so "the same prompt" is a
checkable claim, not a promise. Their trees are ``<repo>-wt-<n>-<tag>`` on
``fix/issue-<n>-<tag>``, with a random six-hex-digit tag: an ordinal (``-a``,
``-2``) would tell each child that another run exists and which one it is. A
twin that runs ``git worktree list`` can still see the other tree; nothing
short of two clones hides that, and nothing in its prompt points there.

**What a twin can still learn, and what the pair records of it (#453).** The
six-pair pilot of #439 showed the pair is not sealed. Three of its twelve
twins named their sibling's tree or branch — from ``ps`` (the other tree's
``jest`` and ``tsc``), from ``git worktree list``/``git branch``, and once
through the other's auto-memory — and one ``cd``-ed into the sibling's tree to
read its ``git status``. Three twins wrote Claude Code auto-memory, which the
held briefing does not cover, and which a sibling reading the same directory
picks up mid-run. One twin asked a question and was answered, its sibling was
not. So:

- Twins start with Claude Code's auto-memory **off** —
  :data:`SETTINGS`, in the child's own ``--settings`` file, which a wake keeps.
  That is the one channel that can be closed without touching the child's
  tools: with it off a twin neither reads the maintainer's memory nor writes
  its own, so both arms get the same nothing, and neither can leave a note the
  other reads. Everything else a normal child has, a twin keeps.
- Seeing the sibling is **not** closed: ``ps``, the parent directory and the
  repo's own refs all show it, and hiding them takes separate clones — which
  ``show`` and ``deliver`` do not know how to read — or a sandbox. And a
  question to the maintainer is part of how a child works, so a twin may
  still ask. Both are **recorded** instead: the first ``show`` of a finished
  pair reads each twin's transcript and appends a ``conditions`` event —
  human turns and answered questions after the opening prompt, whether the
  sibling's tree, branch or id appears anywhere in it, and any file tool
  pointed at an auto-memory directory. ``tools/measure_child_pairs.py``
  reports them and can leave those pairs out.

**Neither publishes.** Twins are dispatched with no grant (``--may none``):
the choice between them is the maintainer's, made after reading both. That
also keeps them outside #436's PR follow, which only wakes children granted
``push``.

**Blind preference.** ``mnemo twins show`` prints the two diffs labelled only
``A`` and ``B``, in an order drawn once per pair from the system's random
source and recorded, so a second ``show`` shows the same order. Each twin's
tag, branch, tree name and session id are replaced in the diff text by the
same placeholder, so a child that wrote its own path into a file does not
sign its work. Under each diff is that twin's closing report — the last text
it wrote, which its prompt tells it to write as "the only copy" — scrubbed
the same way: a twin that delivers nothing on purpose (the issue had already
shipped, or it stopped for approval) says why only there, and a bare "no
commits" cannot be judged next to a diff. ``mnemo twins prefer <pair> A|B|tie`` records the answer and
only then says which twin was which. One answer per pair: an answer changed
after the labels are known is no longer blind. ``show`` refuses while either
twin is still working, because a half-finished diff is not what is being
judged, and it snapshots both twins' output tokens and wall time at that
moment, so the measurement survives Claude Code pruning the jobs.

**The losing twin does not teach the vault.** Every stopped child writes a
SessionEnd briefing and extraction learns from it. A twin's briefing is
*held* instead: its SessionEnd records where the transcript is and writes
nothing. Only the twin the maintainer delivers is briefed — at delivery, from
the held record, or by its own SessionEnd if it is still running then. The
twin that is not delivered did work that never shipped, and its reasoning,
taught to the vault, would be a lesson from a diff nobody accepted; worse, the
two briefings of one issue would count as two pieces of evidence for whatever
they share. That is the contamination that sank the replay design in #439,
arriving by a different road. Its transcript is kept by Claude Code, so a
maintainer who decides otherwise can still run ``mnemo briefing`` on it by
hand. Held on the *shape* of the cwd, not on the ledger, so a ledger that
could not be written fails closed: nothing is learned, nothing is lost.

**Delivery.** ``mnemo deliver <short id>`` delivers a twin like any child,
with two refusals: not before a preference is recorded (delivering first
would tell the maintainer which diff is whose before the blind read), and not
a second twin of a pair already delivered (one issue, one PR).

Recorded as an append-only event log beside the other dispatch records,
``<vault>/.mnemo/dispatch-twins.jsonl``. ``tools/measure_child_pairs.py``
reads it.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import secrets
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from mnemo.core import dispatch

LOG_NAME = "dispatch-twins.jsonl"

#: The labels a blind reader sees, in the order printed.
LABELS = ("A", "B")

#: What a reader may answer, besides a label.
TIE = "tie"

#: What stands in for a twin's tag, branch, tree or id in a blind diff.
PLACEHOLDER = "<twin>"

#: Process phases after which a twin will not change its tree again.
_FINISHED = frozenset({"done", "stopped"})

#: What every twin's settings file adds (#453). Auto-memory off: the twin
#: neither reads the maintainer's memory nor writes to it, so the held
#: briefing is not undone by a note the losing twin left there, and the
#: sibling cannot read one mid-run. Checked against 2.1.280 with ``--settings``:
#: the system prompt names a memory directory without it and none with it.
SETTINGS: Dict[str, Any] = {"autoMemoryEnabled": False}

#: File tools, and the input key naming the file each one changes.
_FILE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
               "NotebookEdit": "notebook_path"}


class TwinsError(RuntimeError):
    """A twins command could not do what it was asked, with the reason."""


@dataclass(frozen=True)
class Twin:
    """One of a pair: its tag, where it works, and the session running there."""

    tag: str
    tree: str
    branch: str
    short_id: str = ""
    error: str = ""


@dataclass(frozen=True)
class Pair:
    """Everything recorded about one issue run twice, folded from the log."""

    pair: str
    issue: int
    repo_root: str
    base: str
    prompt_sha256: str = ""
    model: Optional[str] = None
    effort: Optional[str] = None
    lean: bool = True
    created_at: str = ""
    #: The extra child settings the twins started with; ``{}`` before #453.
    settings: Dict[str, Any] = field(default_factory=dict)
    twins: Tuple[Twin, ...] = ()
    #: The tags in the order ``A``, ``B`` — set by the first ``show``.
    order: Tuple[str, ...] = ()
    #: ``{tag: {"tokens": int|None, "wall_seconds": float|None}}`` at ``show``.
    metrics: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: ``A``, ``B`` or ``tie``; ``""`` until answered.
    choice: str = ""
    #: The tag the choice names, or ``""`` for a tie or no answer.
    preferred: str = ""
    #: The tag delivered, ``""`` until one is.
    delivered: str = ""
    #: ``{tag: {"jsonl": ..., "agent": ...}}`` for each briefing held.
    held: Dict[str, Dict[str, str]] = field(default_factory=dict)
    released: Tuple[str, ...] = ()
    #: ``{tag: conditions_of(...)}`` — what each twin's transcript showed.
    conditions: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @property
    def started(self) -> Tuple[Twin, ...]:
        """The twins that actually started — a spawn can fail after the first."""
        return tuple(t for t in self.twins if not t.error)

    def twin(self, tag: str) -> Optional[Twin]:
        return next((t for t in self.twins if t.tag == tag), None)

    def label_of(self, tag: str) -> str:
        """The blind label *tag* was shown under, or ``""``."""
        return LABELS[self.order.index(tag)] if tag in self.order else ""


# --- the log ---------------------------------------------------------------


def log_path(vault_root: Path | str) -> Path:
    return Path(vault_root) / ".mnemo" / LOG_NAME


def default_vault() -> Path:
    from mnemo.core import config, paths

    return paths.vault_root(config.load_config())


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _append(vault_root: Path | str, event: Dict[str, Any]) -> None:
    """Append one event. Raises ``OSError`` — callers decide if that is fatal."""
    path = log_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"at": _now(), **event}, sort_keys=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")


def _events(vault_root: Path | str) -> List[Dict[str, Any]]:
    try:
        text = log_path(vault_root).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    out = []
    for raw in text.splitlines():
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if isinstance(event, dict) and isinstance(event.get("pair"), str):
            out.append(event)
    return out


def read_pairs(vault_root: Path | str) -> Dict[str, Pair]:
    """``pair id -> Pair``, oldest first. Never raises.

    Folded from the log. The first ``shown`` and the first ``prefer`` of a
    pair win, and later ones are ignored: the order a reader was shown and the
    answer they gave while blind are facts, and a later line cannot revise
    them.
    """
    pairs: Dict[str, Pair] = {}
    for ev in _events(vault_root):
        kind, pid = ev.get("event"), ev["pair"]
        if kind == "pair":
            try:
                twins = tuple(
                    Twin(tag=str(t["tag"]), tree=str(t["tree"]), branch=str(t["branch"]))
                    for t in ev.get("twins") or []
                )
                pairs[pid] = Pair(
                    pair=pid, issue=int(ev["issue"]), repo_root=str(ev["repo_root"]),
                    base=str(ev["base"]), prompt_sha256=str(ev.get("prompt_sha256") or ""),
                    model=ev.get("model"), effort=ev.get("effort"),
                    lean=bool(ev.get("lean", True)), created_at=str(ev.get("at") or ""),
                    settings=ev["settings"] if isinstance(ev.get("settings"), dict) else {},
                    twins=twins,
                )
            except (KeyError, TypeError, ValueError):
                continue
            continue
        pair = pairs.get(pid)
        if pair is None:
            continue
        if kind == "started":
            tag = ev.get("tag")
            pair = replace(pair, twins=tuple(
                replace(t, short_id=str(ev.get("short_id") or ""),
                        error=str(ev.get("error") or ""))
                if t.tag == tag else t
                for t in pair.twins
            ))
        elif kind == "shown" and not pair.order:
            order = tuple(str(t) for t in ev.get("order") or [])
            if len(order) == len(LABELS) and set(order) <= {t.tag for t in pair.twins}:
                metrics = ev.get("metrics") if isinstance(ev.get("metrics"), dict) else {}
                pair = replace(pair, order=order, metrics=metrics)
        elif kind == "prefer" and pair.order and not pair.choice:
            choice = ev.get("choice")
            if choice in LABELS or choice == TIE:
                preferred = "" if choice == TIE else pair.order[LABELS.index(choice)]
                pair = replace(pair, choice=choice, preferred=preferred)
        elif kind == "delivered" and not pair.delivered:
            pair = replace(pair, delivered=str(ev.get("tag") or ""))
        elif kind == "held":
            tag = str(ev.get("tag") or "")
            held = dict(pair.held)
            held[tag] = {"jsonl": str(ev.get("jsonl") or ""),
                         "agent": str(ev.get("agent") or "")}
            pair = replace(pair, held=held)
        elif kind == "conditions" and isinstance(ev.get("twins"), dict):
            # Per twin, first reading wins: one taken while the transcript was
            # there is not replaced by a later one after it was pruned.
            conditions = dict(pair.conditions)
            for tag, found in ev["twins"].items():
                if isinstance(found, dict) and tag not in conditions:
                    conditions[str(tag)] = found
            pair = replace(pair, conditions=conditions)
        elif kind == "released":
            pair = replace(pair, released=pair.released + (str(ev.get("tag") or ""),))
        pairs[pid] = pair
    return pairs


def resolve(vault_root: Path | str, name: str) -> Pair:
    """The pair *name* means: a pair id, or an issue number with one pair.

    Raises :class:`TwinsError` naming the candidates when an issue has more
    than one pair: picking one would record a preference against a pair the
    maintainer did not mean.
    """
    pairs = read_pairs(vault_root)
    wanted = str(name).strip().lstrip("#")
    if wanted in pairs:
        return pairs[wanted]
    if wanted.isdigit():
        found = [p for p in pairs.values() if p.issue == int(wanted)]
        if len(found) == 1:
            return found[0]
        if found:
            raise TwinsError(
                f"#{wanted} has {len(found)} pairs — name one: "
                + ", ".join(p.pair for p in found)
            )
    raise TwinsError(f"no pair named {name!r} — `mnemo twins` lists them")


def pair_for_tree(
    vault_root: Path | str, tree: Path | str,
) -> Tuple[Optional[Pair], Optional[Twin]]:
    """The pair and twin whose tree is *tree*, or ``(None, None)``."""
    tag = dispatch.twin_tag_for_cwd(tree)
    if tag is None:
        return None, None
    for pair in read_pairs(vault_root).values():
        twin = pair.twin(tag)
        if twin is not None:
            return pair, twin
    return None, None


# --- dispatch --------------------------------------------------------------


def _git(args: Sequence[str], *, cwd: Path | str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                              text=True)
    except (FileNotFoundError, OSError) as exc:
        return subprocess.CompletedProcess(list(args), 1, "", str(exc))


def _head(repo_root: Path | str) -> str:
    result = _git(["rev-parse", "HEAD"], cwd=repo_root)
    sha = result.stdout.strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", sha):
        raise dispatch.DispatchError(
            f"could not resolve the commit to branch both twins from: "
            f"{result.stderr.strip() or 'git rev-parse HEAD failed'}"
        )
    return sha


def _fresh_tags(issue: int, *, repo_root: Path | str, count: int,
                draw: Callable[[], str]) -> List[str]:
    """*count* distinct tags whose trees do not exist yet."""
    tags: List[str] = []
    for _ in range(count * 50):
        tag = draw()
        if tag in tags or dispatch.worktree_path(issue, repo_root=repo_root, tag=tag).exists():
            continue
        tags.append(tag)
        if len(tags) == count:
            return tags
    raise dispatch.DispatchError("could not draw distinct twin tags")


def dispatch_twins(
    issue: int, *, repo_root: Path | str,
    fetch: dispatch.Fetcher = dispatch.fetch_issue,
    model: Optional[str] = None, lean: bool = True, effort: Optional[str] = None,
    vault_root: Path | str | None = None,
    draw: Callable[[], str] = dispatch.new_twin_tag,
) -> Tuple[str, List[dispatch.Dispatched]]:
    """Dispatch *issue* as two blind twins. Returns the pair id and both outcomes.

    Refusals come before anything exists: the issue is read, the base commit
    resolved and the pair recorded before either tree is made. The pair is
    written *first* so a twin that ends before the second one has spawned is
    still known to be a twin; its briefing is held either way, on the shape
    of its cwd.

    A twin that fails to start is reported and recorded; the other keeps
    running. It is not stopped: it is doing real work, and a lone twin can
    still be delivered once the maintainer has looked at it — it just is not
    a pair, and ``tools/measure_child_pairs.py`` leaves it out.
    """
    details = fetch(issue, repo_root=repo_root)  # before any git state exists
    base = _head(repo_root)
    vault = default_vault() if vault_root is None else Path(vault_root)
    prompt = dispatch.build_prompt(
        issue, title=details.title, body=details.body, repo_root=repo_root,
        blind=True,
    )
    tags = _fresh_tags(issue, repo_root=repo_root, count=len(LABELS), draw=draw)
    pair_id = secrets.token_hex(3)
    twins = [
        Twin(
            tag=tag,
            tree=str(dispatch.worktree_path(issue, repo_root=repo_root, tag=tag)),
            branch=dispatch.branch_name(issue, tag=tag),
        )
        for tag in tags
    ]
    try:
        _append(vault, {
            "event": "pair", "pair": pair_id, "issue": issue,
            "repo_root": str(repo_root), "base": base,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "model": model, "effort": effort, "lean": lean, "settings": SETTINGS,
            "twins": [{"tag": t.tag, "tree": t.tree, "branch": t.branch} for t in twins],
        })
    except OSError as exc:
        # Refused, not degraded: a pair nobody can find again is a pilot run
        # whose result cannot be read back, which is the whole of its value.
        raise dispatch.DispatchError(f"could not record the pair: {exc}") from exc

    out: List[dispatch.Dispatched] = []
    for twin in twins:
        try:
            tree = dispatch.ensure_worktree(
                issue, repo_root=repo_root, tag=twin.tag, base=base,
            )
            result = dispatch._spawn_into(
                issue, tree, prompt, repo_root=repo_root, branch=twin.branch,
                model=model, lean=lean, may=(), effort=effort, settings=SETTINGS,
            )
        except dispatch.DispatchError as exc:
            result = dispatch.Dispatched(issue=issue, error=str(exc))
        out.append(result)
        try:
            _append(vault, {
                "event": "started", "pair": pair_id, "tag": twin.tag,
                "short_id": result.short_id or "", "error": result.error or "",
            })
        except OSError:
            pass  # the tree and the `pair` line still name this twin
    return pair_id, out


# --- blind reading ---------------------------------------------------------


def _state(short_id: str) -> Optional[Dict[str, Any]]:
    """The twin's ``state.json``, or ``None`` when there is none to read."""
    if not short_id:
        return None
    from mnemo.core.sessions.jobs import jobs_dir

    try:
        data = json.loads((jobs_dir() / short_id / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _seconds_between(start: Any, end: Any) -> Optional[float]:
    try:
        a = _dt.datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        b = _dt.datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    seconds = (b - a).total_seconds()
    return seconds if seconds >= 0 else None


def metrics_of(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Output tokens and wall time out of one ``state.json``.

    ``tokens`` is Claude Code's own counter, which #439 measured to be the
    cumulative output tokens (median ratio 1.0004 to the transcript's sum,
    109 children). Wall time is ``createdAt`` to ``firstTerminalAt`` — the
    first time the child finished — falling back to ``lastTerminalAt`` and
    then ``updatedAt`` when Claude Code did not write the earlier one.
    """
    if not state:
        return {"tokens": None, "wall_seconds": None}
    tokens = state.get("tokens")
    end = (state.get("firstTerminalAt") or state.get("lastTerminalAt")
           or state.get("updatedAt"))
    return {
        "tokens": tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None,
        "wall_seconds": _seconds_between(state.get("createdAt"), end),
    }


def still_working(pair: Pair, *, state: Callable[[str], Optional[Dict[str, Any]]] = _state) -> List[str]:
    """Tags of the twins whose session is on disk and not finished."""
    busy = []
    for twin in pair.started:
        data = state(twin.short_id)
        if data is not None and data.get("state") not in _FINISHED:
            busy.append(twin.tag)
    return busy


def _scrub(text: str, pair: Pair) -> str:
    """*text* with every twin's own names replaced by :data:`PLACEHOLDER`.

    Longest first, so a branch is replaced whole rather than leaving its
    prefix around a replaced tag.
    """
    names = set()
    for twin in pair.twins:
        names.update({twin.branch, Path(twin.tree).name, twin.tree, twin.tag})
        if twin.short_id:
            names.add(twin.short_id)
    for name in sorted((n for n in names if n), key=len, reverse=True):
        text = text.replace(name, PLACEHOLDER)
    return text


def transcript_of(
    pair: Pair, twin: Twin, *,
    state: Callable[[str], Optional[Dict[str, Any]]] = _state,
) -> Optional[Path]:
    """*twin*'s transcript on disk, or ``None``.

    ``state.json``'s ``linkScanPath`` first — a wake writes a new file that
    carries the old history, and this names the newest — then the path its
    SessionEnd recorded when it held the briefing.
    """
    candidates = [((state(twin.short_id) or {}).get("linkScanPath")),
                  (pair.held.get(twin.tag) or {}).get("jsonl")]
    for path in candidates:
        if isinstance(path, str) and path and Path(path).is_file():
            return Path(path)
    return None


def sibling_names(pair: Pair, twin: Twin) -> List[str]:
    """What names *twin*'s sibling, in any text the twin could have read.

    ``-<issue>-<tag>`` is in both the sibling's tree
    (``<repo>-wt-<n>-<tag>``, which ``ps``, ``ls ..`` and ``git worktree
    list`` print) and its branch (``fix/issue-<n>-<tag>``, which ``git
    branch`` prints); its short id is what ``claude agents`` prints. A bare
    six-hex tag would also match inside any commit hash.
    """
    names = []
    for other in pair.twins:
        if other.tag == twin.tag:
            continue
        names.append(f"-{pair.issue}-{other.tag}")
        if other.short_id:
            names.append(other.short_id)
    return names


def conditions_of(transcript: Optional[Path], *, sibling: Sequence[str] = ()) -> Optional[Dict[str, Any]]:
    """What reached one twin besides its prompt, read from its transcript.

    ``None`` when there is no transcript to read. Otherwise:

    - ``human_turns``: turns :func:`mnemo.core.sessions.detector.is_human_turn`
      counts as a person (or a session speaking for one) after the twin's
      first reply — the opening prompt is not input *during* the run;
    - ``answered_questions``: ``AskUserQuestion`` calls that came back with an
      answer rather than an error — the #329 twin's "Remover o campo";
    - ``saw_sibling``: whether any of *sibling* (:func:`sibling_names`)
      appears anywhere in it — a command's output, the twin's own words or a
      tool's input;
    - ``memory_writes``: file-tool calls aimed at a Claude Code auto-memory
      directory (``~/.claude/projects/<project>/memory/``).
    """
    from mnemo.core.sessions import detector

    if transcript is None:
        return None
    found: Dict[str, Any] = {"human_turns": 0, "answered_questions": 0,
                             "saw_sibling": False, "memory_writes": 0}
    asked = set()
    replied = False
    try:
        with open(transcript, encoding="utf-8") as fh:
            for line in fh:
                if not found["saw_sibling"] and any(n and n in line for n in sibling):
                    found["saw_sibling"] = True
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict) or record.get("isSidechain"):
                    continue
                content = (record.get("message") or {}).get("content")
                blocks = [b for b in content if isinstance(b, dict)] \
                    if isinstance(content, list) else []
                if record.get("type") == "assistant":
                    replied = True
                    for block in blocks:
                        if block.get("type") != "tool_use":
                            continue
                        name = block.get("name")
                        if name == "AskUserQuestion":
                            asked.add(block.get("id"))
                        elif name in _FILE_TOOLS:
                            target = str((block.get("input") or {}).get(_FILE_TOOLS[name]) or "")
                            target = target.replace("\\", "/")
                            if "/.claude/projects/" in target and "/memory/" in target:
                                found["memory_writes"] += 1
                elif record.get("type") == "user":
                    if replied and detector.is_human_turn(record):
                        found["human_turns"] += 1
                    for block in blocks:
                        if (block.get("type") == "tool_result"
                                and block.get("tool_use_id") in asked
                                and not block.get("is_error")):
                            found["answered_questions"] += 1
    except OSError:
        return None
    return found


def human_input(found: Optional[Dict[str, Any]]) -> Optional[bool]:
    """Whether *found* (:func:`conditions_of`) shows a person reached the twin."""
    if not isinstance(found, dict):
        return None
    return bool(found.get("human_turns") or found.get("answered_questions"))


def describe(found: Optional[Dict[str, Any]]) -> str:
    """*found* in words, ``""`` when nothing reached the twin."""
    if not isinstance(found, dict):
        return "transcript not read"
    parts = []
    if found.get("answered_questions"):
        parts.append(f"{found['answered_questions']} question(s) answered")
    if found.get("human_turns"):
        parts.append(f"{found['human_turns']} human turn(s)")
    if found.get("saw_sibling"):
        parts.append("saw its sibling")
    if found.get("memory_writes"):
        parts.append(f"{found['memory_writes']} auto-memory write(s)")
    return ", ".join(parts)


def _record_conditions(
    vault_root: Path | str, pair: Pair,
    state: Callable[[str], Optional[Dict[str, Any]]],
) -> None:
    """Append what each finished twin's transcript shows, once per twin.

    Only twins not yet read, and only readings that found a transcript: a
    pruned one is left unknown rather than recorded as clean. Never raises —
    the blind read goes ahead without it.
    """
    readings = {}
    for twin in pair.started:
        if twin.tag in pair.conditions:
            continue
        found = conditions_of(transcript_of(pair, twin, state=state),
                              sibling=sibling_names(pair, twin))
        if found is not None:
            readings[twin.tag] = found
    if readings:
        try:
            _append(vault_root, {"event": "conditions", "pair": pair.pair,
                                 "twins": readings})
        except OSError:
            pass


def _report(pair: Pair, twin: Twin,
            state: Callable[[str], Optional[Dict[str, Any]]]) -> str:
    """*twin*'s closing report, framed for the blind read (not yet scrubbed)."""
    from mnemo.core.sessions.report_card import closing_report

    text = closing_report(transcript_of(pair, twin, state=state))
    body = text.rstrip() + "\n" if text else \
        "(no closing report — its transcript is gone or holds no text)\n"
    return f"\n{'-' * 24} closing report {'-' * 24}\n{body}"


def _diff(pair: Pair, twin: Twin) -> str:
    """What *twin* would deliver: its branch against the pair's base."""
    result = _git(["diff", "--stat", "--patch", pair.base, twin.branch],
                  cwd=pair.repo_root)
    if result.returncode != 0:
        return f"(no diff: {result.stderr.strip() or 'git diff failed'})\n"
    text = result.stdout
    if not text.strip():
        # Not "delivered nothing": two of the six pilot twins committed
        # nothing on purpose, and the reason is in the report under this.
        text = "(no commits on this branch — its closing report says why)\n"
    if Path(twin.tree).is_dir():
        status = _git(["status", "--porcelain"], cwd=twin.tree)
        if status.returncode == 0 and status.stdout.strip():
            text += "\n(the tree also holds uncommitted changes, not shown: " \
                    "they are not what would be delivered)\n"
    return text


def show(
    vault_root: Path | str, name: str, *,
    rng: Optional[Any] = None,
    state: Callable[[str], Optional[Dict[str, Any]]] = _state,
) -> str:
    """The two diffs of pair *name*, labelled only ``A`` and ``B``, each
    followed by that twin's closing report, scrubbed like the diff.

    The first call draws the order and snapshots both twins' metrics; every
    later call reuses them. Any call that finds a twin's conditions unread
    records them (:func:`conditions_of`), so a pair shown before #453 gets
    them the next time it is shown. Raises :class:`TwinsError` for a pair
    that is not two finished twins.
    """
    pair = resolve(vault_root, name)
    if len(pair.started) != len(LABELS):
        raise TwinsError(
            f"pair {pair.pair} has {len(pair.started)} twin(s) running, not "
            f"{len(LABELS)} — there is nothing to compare"
        )
    busy = still_working(pair, state=state)
    if busy:
        raise TwinsError(
            f"pair {pair.pair}: {len(busy)} twin(s) still working — "
            "judge the finished diffs, not a half-written one"
        )
    if not pair.order:
        order = [t.tag for t in pair.started]
        (rng or secrets.SystemRandom()).shuffle(order)
        metrics = {t.tag: metrics_of(state(t.short_id)) for t in pair.started}
        try:
            _append(vault_root, {"event": "shown", "pair": pair.pair,
                                 "order": order, "metrics": metrics})
        except OSError as exc:
            raise TwinsError(f"could not record the order shown: {exc}") from exc
        pair = resolve(vault_root, pair.pair)
    if any(t.tag not in pair.conditions for t in pair.started):
        _record_conditions(vault_root, pair, state)

    parts = [f"pair {pair.pair} — issue #{pair.issue}, both from {pair.base[:12]}\n"]
    for label, tag in zip(LABELS, pair.order):
        twin = pair.twin(tag)
        parts.append(f"\n{'=' * 30} {label} {'=' * 30}\n")
        parts.append(_scrub(_diff(pair, twin) + _report(pair, twin, state), pair))
    parts.append(
        f"\nWhich would you merge? mnemo twins prefer {pair.pair} A|B|tie\n"
        "(one answer per pair, recorded before the labels are revealed)\n"
    )
    return "".join(parts)


def prefer(vault_root: Path | str, name: str, choice: str) -> Pair:
    """Record the blind answer for pair *name*. Returns the pair, answered."""
    answer = choice.strip()
    answer = TIE if answer.lower() == TIE else answer.upper()
    if answer not in LABELS and answer != TIE:
        raise TwinsError(f"answer {choice!r}: say A, B or tie")
    pair = resolve(vault_root, name)
    if not pair.order:
        raise TwinsError(
            f"pair {pair.pair} has not been shown yet — read it first: "
            f"mnemo twins show {pair.pair}"
        )
    if pair.choice:
        raise TwinsError(
            f"pair {pair.pair} already answered {pair.choice} — an answer "
            "given after the labels are known is no longer blind"
        )
    try:
        _append(vault_root, {"event": "prefer", "pair": pair.pair, "choice": answer})
    except OSError as exc:
        raise TwinsError(f"could not record the answer: {exc}") from exc
    return resolve(vault_root, pair.pair)


# --- delivery and the held briefing ----------------------------------------


def delivery_refusal(vault_root: Path | str, tree: Path | str) -> Optional[str]:
    """Why *tree*'s twin may not be delivered yet, or ``None`` when it may.

    ``None`` too for a tree that is not a twin's: this gate is for twins only.
    """
    tag = dispatch.twin_tag_for_cwd(tree)
    if tag is None:
        return None
    pair, _twin = pair_for_tree(vault_root, tree)
    if pair is None:
        # A twin's tree with no pair on record: the log was lost or never
        # written. Nothing blind can be read back for it, so nothing is
        # protected by refusing; say so and let it through.
        return None
    if not pair.choice and len(pair.started) == len(LABELS):
        return (f"a twin of pair {pair.pair}, not judged yet — read both blind "
                f"first: mnemo twins show {pair.pair}")
    if pair.delivered and pair.delivered != tag:
        return f"pair {pair.pair} already delivered its other twin — one issue, one PR"
    return None


def record_delivered(vault_root: Path | str, tree: Path | str,
                     *, spawn: Optional[Callable[[str, str], None]] = None) -> bool:
    """Record that *tree*'s twin was delivered and release its briefing.

    Returns True when a held briefing was released now. When nothing is held
    the twin has not ended yet, and its own SessionEnd — which reads this
    record — writes the briefing. Never raises.
    """
    pair, twin = pair_for_tree(vault_root, tree)
    if pair is None or twin is None:
        return False
    try:
        if pair.delivered != twin.tag:
            _append(vault_root, {"event": "delivered", "pair": pair.pair,
                                 "tag": twin.tag})
        held = pair.held.get(twin.tag)
        if not held or twin.tag in pair.released or not held.get("jsonl"):
            return False
        if spawn is None:
            from mnemo.hooks import session_end

            spawn = session_end._spawn_detached_briefing
        spawn(held["jsonl"], held["agent"])
        _append(vault_root, {"event": "released", "pair": pair.pair, "tag": twin.tag})
        return True
    except Exception:  # noqa: BLE001 — the PR is open; this must not fail it
        return False


def holds(vault_root: Path | str, cwd: Optional[str]) -> bool:
    """True when a session in *cwd* is a twin whose briefing must be held.

    Decided on the cwd's shape first: every twin is held unless the log says
    it is the one delivered. A lost log therefore holds, never teaches.
    """
    tag = dispatch.twin_tag_for_cwd(cwd)
    if tag is None:
        return False
    pair, _twin = pair_for_tree(vault_root, cwd)
    return pair is None or pair.delivered != tag


def hold_briefing(vault_root: Path | str, *, cwd: Optional[str],
                  jsonl: Any, agent: str) -> bool:
    """Hold this session's briefing if it is an undelivered twin's.

    Returns True when held — the caller then writes no briefing. Records
    what a later release needs; a record that cannot be written still holds.
    """
    if not holds(vault_root, cwd):
        return False
    pair, twin = pair_for_tree(vault_root, cwd)
    if pair is not None and twin is not None:
        try:
            _append(vault_root, {"event": "held", "pair": pair.pair, "tag": twin.tag,
                                 "jsonl": str(jsonl), "agent": agent})
        except OSError:
            pass
    return True
