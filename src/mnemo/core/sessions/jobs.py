"""Parse ``~/.claude/jobs/*/state.json`` into :class:`Session` records.

The only module that knows the on-disk schema, so an upstream change touches
one file.

``state`` and ``tempo`` are different axes and the distinction carries the
feature:

- ``state`` is the process phase: ``working``, ``blocked``, ``done``,
  ``stopped``
- ``tempo`` is whether a human is needed: ``blocked``, ``active``, ``idle``

Branch on ``tempo``, never on ``state`` — and note *why* that is sharper than
it sounds: the two vocabularies **overlap**. ``state`` carries its own
``blocked``, so the axes are not orthogonal and a filter written against
``state`` looks plausible on review while being wrong. A blocked session
reads ``state=blocked, tempo=blocked``; branching on ``state`` files it under
a phase and the queue stops surfacing the only sessions it exists to surface.

The enumerations above are measured, not assumed — nine real sessions,
2026-09-13::

    state: {'done': 4, 'blocked': 2, 'stopped': 2, 'working': 1}
    tempo: {'idle': 6, 'blocked': 2, 'active': 1}

An earlier version of this docstring listed ``state`` as ``working, done``
alone. Under-stating a field's range is what made a wrong filter look correct
on review: it cost the incident recorded below on 2026-09-12 and a silent
watcher on 2026-09-13 (#222). Both values it omitted are terminal-ish phases
Claude Code writes routinely, so re-measure before trusting this list again.

Consumers outside Python read the same rule from ``mnemo sessions --json``,
which emits the derived booleans below alongside the raw fields. Nothing
should re-implement them.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


def jobs_dir() -> Path:
    """Where Claude Code keeps background-session state."""
    return Path(os.path.expanduser("~/.claude/jobs"))


@dataclass(frozen=True)
class Session:
    """One background session, as much of it as the file actually had."""

    short_id: str
    state: str | None = None
    tempo: str | None = None
    needs: str | None = None
    detail: str | None = None
    suggested_reply: str | None = None
    name: str | None = None
    intent: str | None = None
    cwd: str | None = None
    #: Claude Code's own ``tokens`` from ``state.json``, copied as-is. **Not**
    #: the context size — it sat 3-65x below it on every job measured (#307).
    #: Kept for consumers already reading it; render ``context_tokens``.
    tokens: int | None = None
    session_id: str | None = None
    link_scan_path: str | None = None
    updated_at: str | None = None
    children: tuple[dict[str, Any], ...] = ()
    live: bool | None = None
    #: The model this session runs on, read from ``respawnFlags`` (#268).
    #: ``None`` only when the flags do not name one — every real session
    #: measured on 2.1.270 did, including those spawned with no ``--model``
    #: at all, because Claude Code resolves the machine default into them.
    model: str | None = None
    #: The full id of the session that dispatched this one (#288), or ``None``
    #: for a session nobody dispatched from inside Claude Code. Not in
    #: ``state.json``: mnemo records it at dispatch, and
    #: :func:`mnemo.core.sessions.parents.stamp` fills it in.
    parent_session: str | None = None
    #: How full this session's context is — the number ``/context`` prints,
    #: read off the transcript's last real model turn (#307). Not in
    #: ``state.json``: :func:`mnemo.core.activity.context.measure` fills
    #: it in. ``None`` when there is no transcript or no turn in it yet.
    context_tokens: int | None = None
    #: ``{tool name: tokens}`` its results put in that context, largest first
    #: (#308). Estimated from the transcript and capped per turn by how much
    #: the context actually grew — not ``/context``'s chars/4, which counts a
    #: screenshot's base64 as text. Filled in with ``context_tokens`` by
    #: :func:`mnemo.core.activity.context.measure`; ``None`` whenever
    #: ``context_tokens`` is, ``{}`` when no tool result has reached the model.
    context_breakdown: dict[str, int] | None = None
    #: What this child may publish without asking — ``("push", "pr")`` — as
    #: granted at dispatch (#317). ``()`` for a child granted nothing, and for
    #: every session nobody dispatched. Not in ``state.json``:
    #: :func:`mnemo.core.sessions.grants.stamp` fills it in.
    may: tuple[str, ...] = ()

    @property
    def is_blocked(self) -> bool:
        """True when a human is needed. Driven by ``tempo``, never ``state``."""
        return self.tempo == "blocked"

    @property
    def is_abandoned(self) -> bool:
        """Blocked on disk, but the process behind it is provably gone (#196).

        ``tempo`` records the last thing a process wrote, not a fact about the
        present, so a session that blocks and then dies asks for attention
        forever. Only ``live is False`` — the daemon roster *proving* the pid
        is gone — counts. ``None`` (no roster, nothing to ask) never does.
        """
        return self.is_blocked and self.live is False

    @property
    def is_waiting(self) -> bool:
        """Blocked and not known to be dead: a real claim on the maintainer."""
        return self.is_blocked and not self.is_abandoned

    #: Process phases that mean "this session will not do anything more".
    #: ``stopped`` is as terminal as ``done`` — it is what Claude Code writes
    #: when a process ends without finishing its turn — and omitting it left
    #: two real sessions rendering under "working" with the literal word
    #: ``stopped`` as their activity (#222).
    FINISHED = frozenset({"done", "stopped"})

    @property
    def is_done(self) -> bool:
        """Finished, by process phase. Says nothing about whether it succeeded."""
        return self.state in self.FINISHED

    @property
    def is_stale(self) -> bool:
        """Finished, and the directory it ran in is gone (#292).

        A dispatch child's tree is removed once its PR merges, but Claude Code
        keeps the job record until someone runs ``claude rm``. On 2026-09-15
        that was 48 records, 14 of them rendering as PRONTAS for days — work
        already merged, reading as work ready to look at.

        A blocked session is never stale, whatever its tree: a question the
        maintainer never saw is the one row the queue must not hide. A session
        with no recorded ``cwd`` is never stale either — nothing proves it gone.

        Unlike the other answers this one asks the filesystem, so it costs a
        ``stat`` per call.
        """
        if not self.is_done or self.is_blocked or not self.cwd:
            return False
        return not os.path.isdir(self.cwd)

    def derived(self) -> dict[str, bool]:
        """The answers consumers actually ask, as plain data.

        ``asdict()`` cannot see a ``@property``, so ``--json`` shipped only the
        raw fields and every consumer re-implemented the rule — the failure
        #222 reported. This keeps one definition for the renderer, the
        statusline and JSON alike.
        """
        return {
            "is_blocked": self.is_blocked,
            "is_waiting": self.is_waiting,
            "is_abandoned": self.is_abandoned,
            "is_done": self.is_done,
            "is_stale": self.is_stale,
        }

    @property
    def label(self) -> str:
        """Best available human-readable name, led by the issue when there is one.

        Claude Code names a session itself (``nameSource="auto"``) by inferring
        a title from the transcript. It reads well and loses the one identifier
        the maintainer is tracking: on the 2026-09-12 dispatch, #193's child
        came back as "recall harness hit_slugs migration".

        The issue number is recovered from ``cwd``, which the dispatcher chose
        (``<repo>-wt-<issue>``, see :mod:`mnemo.core.dispatch`) and Claude Code
        already records — so nothing has to be written or reconciled here. A
        path this dispatcher did not name yields ``None`` and the label is
        unchanged.

        ``cwd`` can also encode a contract piece slug (``<repo>-wt-c-<slug>``)
        rather than an issue. ``#`` means "GitHub issue"; a slug shown as
        ``#c-parser`` would read as a missing issue, so it is prefixed bare.
        """
        from mnemo.core.dispatch import issue_for_cwd

        target = issue_for_cwd(self.cwd)
        if isinstance(target, int):
            prefix = f"#{target} "
        elif target:
            prefix = f"{target} "
        else:
            prefix = ""

        if self.name:
            # Padded to 22 columns by render_queue; keep the whole label inside
            # a sane width so one long inferred title cannot skew every row.
            return (prefix + self.name)[:40]
        if self.intent:
            return (prefix + self.intent.replace("\n", " "))[:40]
        return prefix.strip() or self.short_id


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def model_from(data: dict[str, Any]) -> str | None:
    """The model a session runs on, out of its ``state.json`` (#268).

    Read from ``respawnFlags`` — the argv Claude Code would re-spawn this
    session with — and **not** from the sibling ``model`` key, which is
    ``null`` on every real session measured (23 of 23 on 2.1.270), including
    one deliberately spawned with ``--model haiku``. The flags are where the
    answer actually lives.

    They carry a value even when the dispatcher passed nothing **only on a
    full profile**, where Claude Code resolves the machine's default into
    them (``["--model", "opus[1m]"]``; the default children of 2026-09-14
    read ``["--model", "claude-fable-5-1[1m]"]``). A lean child (#270, the
    default since ``spawn_child`` grew a profile) passes its own
    ``--settings``, leaving no user-level default to resolve, and its flags
    carry no ``--model`` at all. ``None`` for such a child is the true
    answer — the model is whatever the child's own settings resolve — and
    callers must render it as unknown rather than as a failure to read.

    Preferring the raw ``model`` key "when it is set" would be a trap rather
    than a fallback: it is the field an upstream change is most likely to
    start filling in with something *different* from what the child is
    running on. One source, named in ``claude_cli``'s ``bg-model-flag``
    assumption, which the live test checks.
    """
    flags = data.get("respawnFlags")
    if not isinstance(flags, list):
        return None
    for index, flag in enumerate(flags):
        if flag == "--model" and index + 1 < len(flags):
            return _str_or_none(flags[index + 1])
    return None


def _parse(short_id: str, data: dict[str, Any]) -> Session:
    children = data.get("children")
    return Session(
        short_id=short_id,
        state=_str_or_none(data.get("state")),
        tempo=_str_or_none(data.get("tempo")),
        needs=_str_or_none(data.get("needs")),
        detail=_str_or_none(data.get("detail")),
        suggested_reply=_str_or_none(data.get("suggestedReply")),
        name=_str_or_none(data.get("name")),
        intent=_str_or_none(data.get("intent")),
        cwd=_str_or_none(data.get("cwd")),
        tokens=data.get("tokens") if isinstance(data.get("tokens"), int) else None,
        session_id=_str_or_none(data.get("sessionId")),
        link_scan_path=_str_or_none(data.get("linkScanPath")),
        updated_at=_str_or_none(data.get("updatedAt")),
        model=model_from(data),
        children=tuple(c for c in children if isinstance(c, dict)) if isinstance(children, list) else (),
    )


def normalize_cwd(path: str | None) -> str | None:
    """Canonical form of *path*, or ``None`` when there is nothing to compare.

    A worktree, or ``/tmp`` on macOS, reaches one directory through two
    different strings. Comparing them raw empties the queue and the user reads
    that as "nothing is running". ``realpath`` resolves symlinks and strips a
    trailing slash, and a real path always resolves to itself, so normalizing
    a value that is already canonical is a no-op.
    """
    if not path:
        return None
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):  # pragma: no cover - realpath is total on posix
        return path


def _repo_root(path: str | None) -> str | None:
    """The repo a dispatch worktree belongs to, or *path* when it is not one.

    ``dispatch.worktree_path`` puts a child in ``<repo>-wt-<issue>`` beside
    the repo it came from, so the directory name carries the parent's identity
    and nothing has to be written down to recover it.

    Only the two shapes dispatch itself writes are folded — the parsing is
    ``dispatch.issue_for_cwd``, whose regex is anchored so a hand-made
    ``mnemo-wt-feature`` stays its own scope. A prefix match would be the
    obvious shortcut and is wrong: it files ``clubinho-old`` under
    ``clubinho``, putting an unrelated repo's sessions in this queue.
    """
    if not path:
        return None
    from mnemo.core.dispatch import WORKTREE_SUFFIX, issue_for_cwd

    if issue_for_cwd(path) is None:
        return path
    # `-wt-` cannot be absent here: issue_for_cwd matched on it.
    return str(path).rstrip("/").rsplit(WORKTREE_SUFFIX, 1)[0]


def in_scope(session_cwd: str | None, scope: str | None) -> bool:
    """Whether a session started in *session_cwd* belongs to *scope*'s queue.

    A repo and its dispatch worktrees are **one** queue: the maintainer types
    ``mnemo sessions`` in ``~/github/clubinho`` and every child it dispatched
    lives in ``~/github/clubinho-wt-<issue>``. Comparing the two directories
    for equality — what this did until #281 — prints an empty queue while a
    child sits blocked asking a question, and an empty queue reads as "nothing
    is running". The bug was not that the filter was wrong; it was that being
    wrong looked exactly like being right.

    Both sides are folded to their repo, so the relation is symmetric: a child
    running this inside its own worktree sees its siblings, and still sees
    itself. A session with no recorded ``cwd`` never matches a scoped query.
    """
    if scope is None:
        return True
    if session_cwd is None:
        return False
    return _repo_root(normalize_cwd(session_cwd)) == _repo_root(scope)


def read_sessions(root: Path | None = None, *, cwd: str | None = None,
                  claude_home: Path | None = None) -> list[Session]:
    """Every readable background session under *root* (default: real jobs dir).

    Unreadable or malformed entries are skipped, never raised: one corrupt
    file must not take out the whole listing, and a directory we cannot list
    at all reads the same as one that is not there. Pass *cwd* to keep only
    sessions belonging to that directory's queue — the directory itself and
    the dispatch worktrees beside it, see :func:`in_scope`.

    Each session is stamped with ``live`` from the daemon roster (#196). The
    roster is read **once** for the whole listing: the statusline calls this
    on every render under a 2s timeout, so one extra file read per queue is
    the budget, not one per session.
    """
    base = jobs_dir() if root is None else root
    if not base.is_dir():
        return []

    try:
        entries = sorted(base.iterdir())
    except OSError:
        # The directory passed is_dir() and is still unlistable: a permission
        # change, or it vanished between the two calls. Same answer as a
        # missing directory — no sessions, no traceback out of the command.
        return []

    scope = normalize_cwd(cwd)

    # One read for the whole queue. `None` means the roster was unreadable, in
    # which case every session keeps live=None and is treated as waiting.
    from mnemo.core.sessions import liveness as _liveness

    try:
        roster = _liveness.read_roster(claude_home)
    except Exception:  # pragma: no cover - read_roster is already total
        roster = None

    out: list[Session] = []
    for entry in entries:
        if not entry.is_dir():
            continue  # pins.json and friends
        try:
            data = json.loads((entry / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        session = _parse(entry.name, data)
        if cwd is not None and not in_scope(session.cwd, scope):
            continue
        if roster is not None:
            session = replace(session, live=_liveness.is_live(entry.name, roster=roster))
        out.append(session)
    return out
