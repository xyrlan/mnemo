"""Which agent CLI a dispatched Grok session runs on, and where it lives.

A Claude child needs no record of this: Claude Code's own job directory
(``~/.claude/jobs/<short id>/state.json``) names it, its cwd and its id, and
every reader of ``.mnemo/`` already finds it by the short id. A Grok child has
no such directory. What identifies it is a full session id (a UUIDv7), the
Grok home (one per account) whose leader hosts it, and the worktree it was
created in — and none of the three can be derived from the others: the short
id is the last eight hex digits of the full one (``core/grok/paths.short_id``),
the home is whatever account was selected *when it was dispatched*, and the
switch to another account later must not move the child
(``design/specs/2026-10-16-grok-host.md``, decision 3).

So dispatch writes them down once, at spawn, here, and every reader — the
queue, the verbs that wake or stop a child, the notices a parent gets — reads
them from here rather than re-deriving them from the environment of whoever
asks.

**Who gets a line.** Every Grok session dispatch touches:

- each Grok child, when its leader session is created;
- a Grok session that runs ``mnemo dispatch`` itself, so a finish notice can
  reach it through its own leader (its id comes from ``GROK_SESSION_ID``, its
  home from the dispatching process's ``GROK_HOME``). Written by
  :func:`mnemo.core.sessions.parents.record`, once per parent. A reader that
  lists *children* tells the two apart the way it tells a Claude child from
  any other session: a child's ``cwd`` is a dispatch worktree
  (``dispatch.issue_for_cwd``).

A Claude session gets no line, and no line means Claude. That keeps every
ledger byte-identical for a machine that never runs Grok (decision 8), and it
is the truth for every child dispatched before this existed too.

Append-only, one JSON object per line, like the parent and grant logs beside
it; the later line for a short id wins. Writing and reading both fail open:
a record that cannot be written costs the readers this child, never the
dispatch.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional, Union

#: One JSON object per Grok session dispatch spawned or was spawned from.
LOG_NAME = "child-agents.jsonl"

#: The CLIs a line may name. Spelled here rather than imported from
#: ``agent_cli`` so this module reads its own ledger without importing the
#: resolver; ``tests/unit/test_child_agents.py`` pins that the two agree.
AGENTS = ("claude", "grok")


@dataclass(frozen=True)
class ChildAgent:
    """One session's CLI, id and home, as recorded when it was dispatched.

    ``short_id`` is the key every ``.mnemo/`` ledger uses for it (for Grok,
    ``paths.short_id(session_id)``); ``session_id`` is the full id the CLI
    knows it by; ``home`` is the account directory (``GROK_HOME``), ``None``
    where the CLI has none to record; ``cwd`` is where the session runs — a
    child's worktree.
    """

    short_id: str
    agent: str
    session_id: str
    home: Optional[str]
    cwd: str


def log_path(vault_root: Union[Path, str]) -> Path:
    return Path(vault_root) / ".mnemo" / LOG_NAME


def _default_vault() -> Path:
    from mnemo.core import config, paths

    return paths.vault_root(config.load_config())


def record(child: ChildAgent, *, vault_root: Union[Path, str, None] = None) -> None:
    """Append *child*'s line. Never raises.

    A child with no short id or no session id cannot be looked up by either,
    so nothing is written for it.
    """
    if not child.short_id or not child.session_id:
        return
    try:
        path = log_path(_default_vault() if vault_root is None else vault_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(child))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001 — a missing record must not fail a dispatch
        return


def _from_line(entry: object) -> Optional[ChildAgent]:
    if not isinstance(entry, dict):
        return None
    short_id, agent = entry.get("short_id"), entry.get("agent")
    session_id, home, cwd = entry.get("session_id"), entry.get("home"), entry.get("cwd")
    if not (isinstance(short_id, str) and short_id):
        return None
    if agent not in AGENTS:
        # A CLI this version cannot drive: trusting it would send a verb to
        # the wrong program. Skipped, so the reader falls back to "unknown".
        return None
    if not (isinstance(session_id, str) and session_id):
        return None
    if home is not None and not isinstance(home, str):
        return None
    if not isinstance(cwd, str):
        return None
    return ChildAgent(short_id=short_id, agent=agent, session_id=session_id,
                      home=home or None, cwd=cwd)


def read(vault_root: Union[Path, str]) -> Dict[str, ChildAgent]:
    """``short_id -> ChildAgent`` for every session recorded. Never raises.

    A malformed line, or one naming a CLI outside :data:`AGENTS`, is skipped
    rather than ending the read, so one torn append costs one session and not
    every one after it. When a short id appears twice the later line wins.
    """
    out: Dict[str, ChildAgent] = {}
    try:
        text = log_path(vault_root).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return out
    for raw in text.splitlines():
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        child = _from_line(entry)
        if child is not None:
            out[child.short_id] = child
    return out
