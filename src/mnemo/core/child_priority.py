"""A dispatched child runs below the user's CPU priority (#723).

Nobody chose the priority a child ran at: it was the OS default, the same as
the user's. One child running a test suite that starts a harness per logical
CPU took a 16-thread workstation to 100% and the desktop with it — the
desktop dispatch is driven from. The child's work belongs in the background.

**Who lowers whom.** Only mnemo knows a session is a background child, so the
dispatcher marks it and the child lowers itself, from mnemo's own
``SessionStart`` hook. Measured 2026-10-17 on Windows (issue #723 and its
measurement comment):

- ``claude --bg`` has WMI create the daemon (its parent is ``WmiPrvSE.exe``),
  so a priority class passed when *spawning* reaches nothing, cold daemon or
  warm. Lowering the daemon works but it is shared: the operator's own
  ``claude --bg`` sessions would slow down with it.
- Lowering the session process itself does work: everything it starts
  afterwards inherits ``BELOW_NORMAL``, because on Windows a process of that
  class passes it to the children it creates without explicit flags. What was
  already running keeps Normal (the mnemo MCP server did), so the hook does
  this before its other work.
- An ``env`` key in the child's ``--settings`` file reaches the hook. That is
  the marker: :data:`ENV`, written by the dispatcher through the settings
  mapping both profiles already take (#453). An interactive session has no
  marker and is left alone, on either profile.

**Which process.** The session is looked up by its id in Claude Code's own
registry (:func:`mnemo.core.sessions.registry.live_entry`), which names the
session's pid and its creation time — never by walking up the hook's parents,
which an npm install (``node.exe``), a deeper shim chain or a reused pid
would send to the wrong process, the shared daemon included. Only an entry of
``kind: "bg"`` is lowered: should an interactive ``claude --resume`` of a
child carry the child's saved settings, its entry says ``interactive`` and
the session it is is left alone. The creation time is checked again on the
handle that sets the class, so a pid reused in between is never touched.

A hook rather than a one-time lowering by the dispatcher because the hook
fires again when the child is woken (``--bg --resume`` restores the saved
``--settings``, and ``SessionStart`` fires on the wake), and a woken child is
a new process at Normal: this session's own wake on 2026-10-17 came back as a
new pid. The hook also reads the config each time, so ``childPriority:
"normal"`` set after a dispatch holds for the child's later wakes.

``BELOW_NORMAL``, never ``IDLE``: an idle suite can be starved into timeouts.

**Windows only.** On POSIX the daemon is probably forked and ``nice`` is
inherited, which turns the risk around (a niced launcher could renice the
shared daemon); that is unmeasured, so the marker is not written there and the
hook does nothing. Grok children are not covered either.

Everything here is best effort: a failure is returned as words for the error
log, never raised, and never changes what ``SessionStart`` prints.
``tools/measure_child_priority.py`` measures the whole path on a throwaway
``--bg`` session.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, Mapping, Optional

#: The marker the dispatcher puts in a child's settings ``env``.
ENV = "MNEMO_CHILD_PRIORITY"
BELOW_NORMAL = "below_normal"
NORMAL = "normal"

_IDLE_PRIORITY_CLASS = 0x0040
_BELOW_NORMAL_PRIORITY_CLASS = 0x4000
_PROCESS_SET_INFORMATION = 0x0200
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def supported() -> bool:
    """True where lowering was measured to work: Windows."""
    return sys.platform == "win32"


def wanted(cfg: Optional[Mapping[str, Any]]) -> bool:
    """False only when ``dispatch.childPriority`` is ``normal``.

    Anything else — an unknown value, a ``dispatch`` that is not an object, no
    config — keeps the default: a typo must neither put a child back in the
    user's way nor abort the dispatch, and ``idle`` is not offered.
    """
    section = cfg.get("dispatch") if isinstance(cfg, Mapping) else None
    raw = section.get("childPriority") if isinstance(section, Mapping) else None
    return not (isinstance(raw, str) and raw.strip().lower() == NORMAL)


def marked(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True when this process carries the dispatcher's marker."""
    env = os.environ if environ is None else environ
    return str(env.get(ENV) or "").strip().lower() == BELOW_NORMAL


def with_marker(
    settings: Optional[Mapping[str, Any]], cfg: Optional[Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    """*settings* with :data:`ENV` added to its ``env``, or *settings* as given.

    Unchanged when the platform is not :func:`supported` or the config opts
    out, so those children start with the argv they always did. Keys already
    there — a twin's ``autoMemoryEnabled``, an ``env`` of its own — are kept.
    """
    if not supported() or not wanted(cfg):
        return dict(settings) if settings else None
    out: Dict[str, Any] = dict(settings or {})
    env = out.get("env")
    out["env"] = {**(env if isinstance(env, dict) else {}), ENV: BELOW_NORMAL}
    return out


def lower_session(
    session_id: str, cfg: Optional[Mapping[str, Any]], *,
    environ: Optional[Mapping[str, str]] = None, registry_dir=None,
) -> Optional[str]:
    """Lower *session_id*'s own process to below-normal when it is a marked child.

    Returns ``None`` when there was nothing to do or it worked, and a short
    reason when it failed. Never raises.
    """
    if not marked(environ) or not supported() or not wanted(cfg):
        return None
    try:
        from mnemo.core.sessions import registry

        if not session_id:
            return "the hook was given no session id"
        entry = registry.live_entry(session_id, directory=registry_dir)
        if entry is None:
            return f"no live entry for session {session_id} in {registry.sessions_dir()}"
        if entry.get("kind") != "bg":
            return None
        return _set_below_normal(entry["pid"], registry.as_int(entry.get("procStart")))
    except Exception as exc:  # noqa: BLE001 — best effort, by contract
        return f"{type(exc).__name__}: {exc}"


def _set_below_normal(pid: int, proc_start: Optional[int]) -> Optional[str]:
    """Lower *pid* if it is still the process created at *proc_start*."""
    import ctypes

    from mnemo.core.sessions import registry

    kernel32 = registry._kernel32()
    handle = kernel32.OpenProcess(
        _PROCESS_SET_INFORMATION | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid,
    )
    if not handle:
        return f"OpenProcess({pid}) failed: error {ctypes.get_last_error()}"
    try:
        if registry.handle_creation_time(kernel32, handle) != proc_start:
            return f"pid {pid} is no longer the session the registry named"
        # Already at or under the target: raising an idle session is not ours.
        if kernel32.GetPriorityClass(handle) in (
            _IDLE_PRIORITY_CLASS, _BELOW_NORMAL_PRIORITY_CLASS,
        ):
            return None
        if not kernel32.SetPriorityClass(handle, _BELOW_NORMAL_PRIORITY_CLASS):
            return f"SetPriorityClass({pid}) failed: error {ctypes.get_last_error()}"
        return None
    finally:
        kernel32.CloseHandle(handle)
