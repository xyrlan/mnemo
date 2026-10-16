"""``mnemo dispatch --read-only`` on Grok: the editing tools closed by mnemo's hook.

On Claude a read-only child is spawned with ``--disallowedTools Edit Write
NotebookEdit``. Grok has nothing that does this for one session: ``session/new``
takes no tool list, tool removal (``--disallowed-tools``) is a flag of the
leader *process* and so of every session of the account, and a deny rule in
the worktree is dropped because the worktree is an untrusted folder
(``xai-grok-shell/src/agent/mvp_agent/session_setup.rs``,
``xai-grok-permission-rules/src/folder_trust.rs`` at grok-build ``2bdd1d6``).

What Grok does honour is a PreToolUse ``permissionDecision: deny`` from a hook
in ``~/.claude/settings.json`` — mnemo's own, already installed (measured on
grok 1.0.60, the Grok round's spec). So dispatch marks the child's session
read-only here (:func:`mark`) after ``session/new`` and *before* its opening
turn, and the hook denies Grok's file-editing tools for a marked session
(:func:`denies`). No user config is written and no folder is trusted.

The meaning is the one ``--read-only`` has on Claude: the editing tools are
closed, the shell still writes. It prevents accident and drift, never intent.

**Who is denied.** The marked session, and a subagent working in the marked
session's worktree. A Grok subagent is a session of its own, with its own id;
its hook payload names no parent, but it carries ``subagentType`` and runs in
its parent's cwd unless told another (``agent/subagent/handle_request.rs``,
``resolve_child_cwd``), and the cwd of a dispatched child is its own worktree,
which no parent and no other session dispatch knows of runs in. A subagent
given a cwd inside that tree is denied too; one Grok isolates in a worktree of
its own is not, and is the one gap. A top-level session that merely opens
that folder carries no ``subagentType`` and is not denied.

A mark is never removed: a child woken or resumed later keeps its id, and must
keep its posture. One small file per read-only child.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Union

#: Under ``<vault>/.mnemo/``: one ``<session id>.json`` per read-only Grok child.
MARK_DIR = "grok-read-only"

#: Grok's file-editing tools, by Grok's own names (``claude_alias.rs``):
#: ``write`` creates a file, ``search_replace`` and ``hashline_edit`` edit one.
#: The three Claude's ``--disallowedTools Edit Write NotebookEdit`` stand for;
#: Grok has no notebook tool.
EDIT_TOOLS = frozenset({"write", "search_replace", "hashline_edit"})

#: What the model reads when a call is denied.
REASON = (
    "mnemo: this session was dispatched --read-only, so its file-editing tools "
    "(write, search_replace, hashline_edit) are closed. Investigate and report; "
    "do not change files."
)

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _default_vault() -> Path:
    from mnemo.core import config, paths

    return paths.vault_root(config.load_config())


def _dir(vault_root: Union[Path, str, None]) -> Path:
    root = _default_vault() if vault_root is None else Path(vault_root)
    return root / ".mnemo" / MARK_DIR


def _mark_path(vault_root: Union[Path, str, None], session_id: str) -> Path:
    return _dir(vault_root) / ("%s.json" % (_SAFE.sub("_", session_id) or "unknown"))


def mark(session_id: str, cwd: Union[Path, str], *,
         vault_root: Union[Path, str, None] = None) -> None:
    """Mark *session_id*, running in *cwd*, read-only. Raises ``OSError``.

    Unlike the other ledgers this one does not fail open: a child whose mark
    could not be written would run with its editing tools open, so dispatch
    closes the session and refuses rather than spawn it.
    """
    if not session_id:
        raise ValueError("a read-only mark needs a session id")
    path = _mark_path(vault_root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".%d.tmp" % os.getpid())
    tmp.write_text(json.dumps({"session_id": session_id, "cwd": str(cwd)}),
                   encoding="utf-8")
    os.replace(str(tmp), str(path))


def is_marked(session_id: str, *, vault_root: Union[Path, str, None] = None) -> bool:
    if not session_id:
        return False
    try:
        return _mark_path(vault_root, session_id).is_file()
    except OSError:
        return False


def _within(path: str, root: str) -> bool:
    """True when *path* is *root* or a directory under it."""
    try:
        p = Path(os.path.normcase(os.path.realpath(path)))
        r = Path(os.path.normcase(os.path.realpath(root)))
    except (OSError, ValueError):
        return False
    return p == r or r in p.parents


def _marked_cwd(cwd: str, vault_root: Union[Path, str, None]) -> bool:
    """True when *cwd* is in the worktree of some read-only child."""
    try:
        entries = list(_dir(vault_root).glob("*.json"))
    except OSError:
        return False
    for entry in entries:
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        marked = data.get("cwd") if isinstance(data, dict) else None
        if isinstance(marked, str) and marked and _within(cwd, marked):
            return True
    return False


def denies(payload: Mapping[str, Any], *,
           vault_root: Union[Path, str, None] = None) -> bool:
    """True when this Grok PreToolUse *payload* is an edit a read-only child may not make.

    *payload* is the raw Grok payload or :func:`mnemo.core.grok.hooks.normalize`'s
    copy of it (which keeps Grok's tool name under ``grok_tool_name``). Any
    other tool, and every session that is neither marked nor a subagent in a
    marked worktree, is let through.
    """
    name = payload.get("grok_tool_name") or payload.get("toolName") or payload.get("tool_name")
    if name not in EDIT_TOOLS:
        return False
    sid = str(payload.get("session_id") or payload.get("sessionId") or "")
    if is_marked(sid, vault_root=vault_root):
        return True
    if not payload.get("subagentType"):
        return False
    cwd: Optional[str] = payload.get("cwd")
    return isinstance(cwd, str) and bool(cwd) and _marked_cwd(cwd, vault_root)
