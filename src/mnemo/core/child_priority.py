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
  this **first**.
- The hook runs as ``… ← bash ← bash ← claude.exe --session-id <id> ←
  claude.exe --bg-pty-host ← claude.exe daemon run``. The **first**
  ``claude.exe`` ancestor is the session, found by executable name alone, so
  the pty host — whose command line also carries ``--session-id`` — is never
  reached and no command line has to be read.
- An ``env`` key in the child's ``--settings`` file reaches the hook. That is
  the marker: :data:`ENV`, written by the dispatcher through the settings
  mapping both profiles already take (#453). An interactive session has no
  marker and is left alone, on either profile.

A hook rather than a one-time lowering by the dispatcher because the hook
fires again when the child is woken (``--bg --resume`` restores the saved
``--settings``, and ``SessionStart`` fires on the wake), while a respawned
session is born from the daemon at Normal and would lose a per-PID change.

``BELOW_NORMAL``, never ``IDLE``: an idle suite can be starved into timeouts.

**Windows only.** On POSIX the daemon is probably forked and ``nice`` is
inherited, which turns the risk around (a niced launcher could renice the
shared daemon); that ancestry is unmeasured, so the marker is not written
there and the hook does nothing. Grok children are not covered either.

Everything here is best effort: a failure is returned as words for the error
log, never raised, and never changes what ``SessionStart`` prints.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, Mapping, Optional, Tuple

#: The marker the dispatcher puts in a child's settings ``env``.
ENV = "MNEMO_CHILD_PRIORITY"
BELOW_NORMAL = "below_normal"
NORMAL = "normal"

#: The executable whose first instance above the hook is the session.
SESSION_EXE = "claude.exe"

#: How far up the hook walks before giving up. The measured chain puts the
#: session four levels up; a launcher shim or another shell adds one or two.
MAX_DEPTH = 8

# Win32 constants.
_TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_SET_INFORMATION = 0x0200
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_IDLE_PRIORITY_CLASS = 0x0040
_BELOW_NORMAL_PRIORITY_CLASS = 0x4000

#: pid -> (parent pid, executable name).
Table = Dict[int, Tuple[int, str]]


def supported() -> bool:
    """True where lowering was measured to work: Windows."""
    return sys.platform == "win32"


def wanted(cfg: Optional[Mapping[str, Any]]) -> bool:
    """False only when ``dispatch.childPriority`` is ``normal``.

    Anything else, an unknown value included, keeps the default: a typo must
    not put a child back in the user's way, and ``idle`` is not offered.
    """
    raw = ((cfg or {}).get("dispatch") or {}).get("childPriority", BELOW_NORMAL)
    return not (isinstance(raw, str) and raw.strip().lower() == NORMAL)


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


def find_session(table: Table, start: int, depth: int = MAX_DEPTH) -> Optional[int]:
    """The first :data:`SESSION_EXE` strictly above *start*, or ``None``.

    Stops at a pid missing from *table*, at a cycle (Windows reuses pids, so a
    parent pid can name a later process) and after *depth* levels.
    """
    seen = {start}
    pid = start
    for _ in range(depth):
        entry = table.get(pid)
        if entry is None:
            return None
        pid = entry[0]
        if pid in seen or pid not in table:
            return None
        seen.add(pid)
        if table[pid][1].lower() == SESSION_EXE:
            return pid
    return None


def lower_session(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Lower this hook's session to below-normal when it is a marked child.

    Returns ``None`` when there was nothing to do or it worked, and a short
    reason when it failed. Never raises.
    """
    env = os.environ if environ is None else environ
    if env.get(ENV, "").strip().lower() != BELOW_NORMAL or not supported():
        return None
    try:
        pid = find_session(_process_table(), os.getpid())
        if pid is None:
            return f"no {SESSION_EXE} within {MAX_DEPTH} levels above the hook"
        return _set_below_normal(pid)
    except Exception as exc:  # noqa: BLE001 — best effort, by contract
        return f"{type(exc).__name__}: {exc}"


def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Declared, not left to ctypes' defaults: an undeclared HANDLE return is
    # truncated to 32 bits on 64-bit Windows (see sessions.liveness).
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetPriorityClass.restype = wintypes.DWORD
    kernel32.GetPriorityClass.argtypes = [wintypes.HANDLE]
    kernel32.SetPriorityClass.restype = wintypes.BOOL
    kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32


def _process_table() -> Table:
    """Every process's parent and executable name, from one Toolhelp snapshot."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = _kernel32()
    snap = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
    table: Table = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            table[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return table


def _set_below_normal(pid: int) -> Optional[str]:
    import ctypes

    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(
        _PROCESS_SET_INFORMATION | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid,
    )
    if not handle:
        return f"OpenProcess({pid}) failed: error {ctypes.get_last_error()}"
    try:
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
