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

**A Grok dispatcher** (the Grok round, ``design/specs/2026-10-16-grok-host.md``).
Grok Build sets ``GROK_SESSION_ID`` in every command its shell tool runs, and
that id wins over ``CLAUDE_CODE_SESSION_ID`` for the reason
:func:`mnemo.core.agent_cli.current_cli` gives: a ``grok`` started inside a
Claude Code session inherits the Claude variable too. The link line is the
same shape either way. What a Grok parent needs besides is its home, because
a notice reaches it through its own account's leader: :func:`record` writes
it once into :mod:`mnemo.core.sessions.child_agents`, keyed by
``paths.short_id``. With no ``GROK_SESSION_ID`` in the environment nothing
here differs from before, byte for byte.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Mapping, Optional

from mnemo.core.sessions.jobs import Session

#: The variable Claude Code sets to the running session's full id.
PARENT_ENV = "CLAUDE_CODE_SESSION_ID"

#: The variable Grok Build sets to the running session's full id, in every
#: command its shell tool runs (``agent_cli.GROK_SESSION_ENV``, measured on
#: grok 1.0.60). Checked first: see the module docstring.
GROK_PARENT_ENV = "GROK_SESSION_ID"

#: One JSON object per dispatched child. Append-only: a line is ~100 bytes, so
#: a thousand dispatches is a tenth of a megabyte, and nothing here is worth
#: the rewrite race that pruning concurrent dispatchers' lines would open.
LOG_NAME = "dispatch-parents.jsonl"


def log_path(vault_root: Path | str) -> Path:
    return Path(vault_root) / ".mnemo" / LOG_NAME


def _env_value(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def parent_from_env(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The session running this process, or ``None`` outside an agent session.

    Grok's ``GROK_SESSION_ID`` first, then Claude Code's
    ``CLAUDE_CODE_SESSION_ID``; an empty or blank value counts as unset.
    """
    env = os.environ if env is None else env
    return _env_value(env, GROK_PARENT_ENV) or _env_value(env, PARENT_ENV) or None


def _grok_parent(env: Mapping[str, str]) -> Optional[str]:
    """The dispatching Grok session's id, when the parent is a Grok session."""
    return _env_value(env, GROK_PARENT_ENV) or None


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
    env = os.environ if env is None else env
    parent = parent_from_env(env)
    if not parent or not short_id:
        return None
    try:
        vault = _default_vault() if vault_root is None else Path(vault_root)
        path = log_path(vault)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"short_id": short_id, "parent_session": parent})
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001 — a missing link must not fail a dispatch
        return None
    if _grok_parent(env) == parent:
        _record_grok_parent(parent, vault_root=vault, env=env)
    return parent


def _record_grok_parent(
    session_id: str, *, vault_root: Path, env: Mapping[str, str],
) -> None:
    """Write the Grok parent's id, home and cwd into the agent ledger, once.

    Once: a dispatch of eight pieces would otherwise write eight identical
    lines. A parent that is itself a dispatched Grok child already has its
    line, with the worktree as its cwd, and keeps it.

    The cwd is the one Grok filed the session under (its ``summary.json``),
    because that is the directory a leader reloads a dormant session in; the
    shell's own cwd may have moved since. ``GROK_WORKSPACE_ROOT``, then this
    process's cwd, when the session's directory cannot be read. Never raises.
    """
    try:
        from mnemo.core.grok import paths as grok_paths
        from mnemo.core.sessions import child_agents

        short_id = grok_paths.short_id(session_id)
        if short_id in child_agents.read(vault_root):
            return
        home = grok_paths.grok_home(env)
        cwd = _grok_session_cwd(session_id, home) or _env_value(env, "GROK_WORKSPACE_ROOT")
        child_agents.record(
            child_agents.ChildAgent(
                short_id=short_id, agent="grok", session_id=session_id,
                home=str(home), cwd=cwd or os.getcwd(),
            ),
            vault_root=vault_root,
        )
    except Exception:  # noqa: BLE001 — the parent link above is already written
        return


def _grok_session_cwd(session_id: str, home: Path) -> Optional[str]:
    from mnemo.core.grok import acp, paths as grok_paths

    found = grok_paths.find_session_dir(session_id, home)
    return acp.session_cwd(found) if found is not None else None


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
