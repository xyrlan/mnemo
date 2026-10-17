"""Which live process is a Claude Code session, from Claude Code's own registry.

Claude Code keeps one file per session process, ``<config dir>/sessions/<pid>.json``
(``$CLAUDE_CONFIG_DIR`` when set, ``~/.claude`` otherwise — #671), holding
among others ``pid``, ``sessionId``, ``procStart``, ``pidDomain`` and
``kind`` (``"bg"`` or ``"interactive"``). ``--bg`` children are in it, and the
entry's ``pid`` is the ``claude.exe --session-id <id>`` process itself, never
its pty host or the daemon (measured 2026-10-17, #723 review).

Three facts make a plain "the file naming this id" wrong, all measured on the
maintainer's machine:

- Files are **not removed** when a session exits: 13 of 18 were dead, some
  written under another hostname (a synced home).
- One ``sessionId`` can be named by **up to four** files, because a resumed
  session is a new process that writes its own (the shape of #454).
- A dead entry's ``pid`` may since have been reused by an unrelated process.

So :func:`live_entry` accepts an entry only when its ``pidDomain`` is this
host, its ``pid`` is alive, and that process's creation time equals
``procStart``. On Windows ``procStart`` is the creation time as a FILETIME
(UTC, 100 ns ticks) — it matched ``(Get-Process).StartTime.ToFileTimeUtc()``
digit for digit on six live pids — and ``pidDomain`` is ``win32:`` plus the
lowercase hostname. What ``procStart`` holds elsewhere is not measured, so
off Windows nothing is confirmed and :func:`live_entry` returns ``None``.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

#: pid -> creation time in the units ``procStart`` uses, or ``None``.
CreationTime = Callable[[int], Optional[int]]


def sessions_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    """``<config dir>/sessions`` for the account this process runs under."""
    from mnemo.core.sessions import accounts

    return (accounts.current(env) or Path.home() / ".claude") / "sessions"


def host_domain() -> str:
    """This machine's ``pidDomain``: ``win32:desktop-8b0ed29`` (measured)."""
    return f"{sys.platform}:{socket.gethostname().lower()}"


def entries(session_id: str, directory: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every readable entry naming *session_id*, dead or alive, in no order."""
    if not session_id:
        return []
    folder = sessions_dir() if directory is None else Path(directory)
    out: List[Dict[str, Any]] = []
    try:
        paths = list(folder.glob("*.json"))
    except OSError:
        return []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("sessionId") == session_id:
            out.append(data)
    return out


def live_entry(
    session_id: str, *, directory: Optional[Path] = None,
    domain: Optional[str] = None, creation_time: Optional[CreationTime] = None,
) -> Optional[Dict[str, Any]]:
    """The one entry for *session_id* whose process is running now, or ``None``.

    Never raises. *domain* and *creation_time* default to this host's.
    """
    try:
        domain = host_domain() if domain is None else domain
        created = creation_time or windows_creation_time
        for entry in entries(session_id, directory):
            pid = entry.get("pid")
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                continue
            if entry.get("pidDomain") != domain:
                continue
            start = as_int(entry.get("procStart"))
            if start is not None and created(pid) == start:
                return entry
    except Exception:  # noqa: BLE001 — a lookup, never a crash
        return None
    return None


def as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def windows_creation_time(pid: int) -> Optional[int]:
    """*pid*'s creation FILETIME while it is running, else ``None`` (and off Windows)."""
    if sys.platform != "win32":
        return None
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        return handle_creation_time(kernel32, handle)
    finally:
        kernel32.CloseHandle(handle)


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def handle_creation_time(kernel32, handle) -> Optional[int]:
    """Creation FILETIME of the running process behind *handle*, else ``None``.

    Read off the handle itself, so a caller that goes on to act on the same
    handle acts on the process it checked, whatever the pid is reused for.
    """
    import ctypes
    from ctypes import wintypes

    code = wintypes.DWORD()
    if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != _STILL_ACTIVE:
        return None
    times = [wintypes.FILETIME() for _ in range(4)]
    if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
        return None
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Declared, not left to ctypes' defaults: an undeclared HANDLE return is
    # truncated to 32 bits on 64-bit Windows (see sessions.liveness).
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.GetPriorityClass.restype = wintypes.DWORD
    kernel32.GetPriorityClass.argtypes = [wintypes.HANDLE]
    kernel32.SetPriorityClass.restype = wintypes.BOOL
    kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32


def env_session_id(env: Optional[Mapping[str, str]] = None) -> str:
    """``CLAUDE_CODE_SESSION_ID`` from *env*, stripped, or ``""``."""
    env = os.environ if env is None else env
    return str(env.get("CLAUDE_CODE_SESSION_ID") or "").strip()
