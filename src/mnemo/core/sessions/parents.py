"""Which session dispatched a child (#288).

Nothing Claude Code writes links a ``--bg`` child to the session that ran
``mnemo dispatch``: the daemon roster's ``dispatch`` record says
``source: "shell"`` with an empty ``env``, and the child's ``state.json``
carries no parent. The parent's id *is* known at dispatch time, because
Claude Code exports ``CLAUDE_CODE_SESSION_ID`` into every command its Bash
tool runs (``parent-session-env`` in :mod:`mnemo.core.claude_cli`). So the
dispatcher writes the link down, and ``mnemo sessions --json`` hands it back
as ``parent_session`` on each row.

**Where it is written, and why not the worktree.** #288 proposed
``<worktree>/.mnemo-child-profile/dispatch.json``. A record inside the tree
dies with the tree, and the tree goes long before the child's job does: on
2026-09-15, 36 of the 43 dispatch children under ``~/.claude/jobs`` had no
worktree left on disk, and among *finished* children it was 34 of 37. The
consumer that asked for this sums children's tokens onto the parent, which is
a number that is only final once a child is finished — precisely the children
whose tree is most likely gone. ``state.json`` outlives the tree (it still
records ``cwd``), so the link has to live somewhere that outlives it too:
mnemo's own ``<vault>/.mnemo/``, keyed by the short id the job directory is
named after.

**What is recorded: the parent, and nothing else.** The proposal also listed
``issue``, ``piece``, ``contract``, ``parent_pid`` and ``created_at``. The
first two are already recovered from ``cwd`` by ``dispatch.issue_for_cwd``,
which works on a removed tree, and a second copy is a second answer to keep in
agreement. ``created_at`` is ``state.json``'s ``createdAt``. ``parent_pid``
names a process, not a session: it outlives nothing and is reused once the
parent exits, and the session id is what the transcript and the job directory
are named after. ``contract`` has no reader. What only the dispatcher knows is
who called it, so that is the record.

A dispatch run from a plain terminal has no parent session: no line is
written and the row reads ``parent_session: null``, which is the truth.

Reading and writing both fail open. A link that cannot be written costs the
maintainer a ``null``, never a dispatch; a log that cannot be read costs the
queue a column, never the queue.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Mapping

from mnemo.core.sessions.jobs import Session

#: The variable Claude Code sets to the running session's full id.
PARENT_ENV = "CLAUDE_CODE_SESSION_ID"

#: One JSON object per dispatched child. Append-only: a line is ~100 bytes, so
#: a thousand dispatches is a tenth of a megabyte, and nothing here is worth
#: the rewrite race that pruning concurrent dispatchers' lines would open.
LOG_NAME = "dispatch-parents.jsonl"


def log_path(vault_root: Path | str) -> Path:
    return Path(vault_root) / ".mnemo" / LOG_NAME


def parent_from_env(env: Mapping[str, str] | None = None) -> str | None:
    """The session running this process, or ``None`` outside Claude Code."""
    value = (os.environ if env is None else env).get(PARENT_ENV, "")
    value = value.strip() if isinstance(value, str) else ""
    return value or None


def _default_vault() -> Path:
    from mnemo.core import config, paths

    return paths.vault_root(config.load_config())


def record(
    short_id: str,
    *,
    vault_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Write down that *short_id* was dispatched by this session. Never raises.

    Returns the parent recorded, or ``None`` when nothing was written — no
    parent session in the environment, no id to key it by, or a vault that
    could not be written. Called only once a child's id has been read back:
    a child whose id is unknown cannot be looked up by it either.
    """
    parent = parent_from_env(env)
    if not parent or not short_id:
        return None
    try:
        path = log_path(_default_vault() if vault_root is None else vault_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"short_id": short_id, "parent_session": parent})
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001 — a missing link must not fail a dispatch
        return None
    return parent


def read(vault_root: Path | str) -> dict[str, str]:
    """``short_id -> parent_session`` for every child recorded. Never raises.

    A malformed line is skipped rather than ending the read, so one torn
    append costs one child its link and not every child after it. When a short
    id appears twice the later line wins, as the more recent dispatch.
    """
    out: dict[str, str] = {}
    try:
        text = log_path(vault_root).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return out
    for raw in text.splitlines():
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        short_id, parent = entry.get("short_id"), entry.get("parent_session")
        if isinstance(short_id, str) and short_id and isinstance(parent, str) and parent:
            out[short_id] = parent
    return out


def stamp(sessions: Iterable[Session], *, vault_root: Path | str) -> list[Session]:
    """*sessions* with ``parent_session`` filled in from the log, read once."""
    parents = read(vault_root)
    return [
        replace(s, parent_session=parents[s.short_id]) if s.short_id in parents else s
        for s in sessions
    ]
