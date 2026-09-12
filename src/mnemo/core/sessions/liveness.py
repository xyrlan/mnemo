"""Is a background session's process still running?

``tempo`` is not a fact about the present. It is a record of the last thing a
process wrote before it stopped writing, so a session that blocks and then
dies leaves ``tempo=blocked`` frozen on disk forever and the queue replays it
as a live request for attention (#196).

``state.json`` carries no pid — the issue was right about that. But the daemon
keeps ``~/.claude/daemon/roster.json``, whose ``workers`` map is keyed by the
**same short id** as the job directories and carries a real pid. So a genuine
liveness probe is available, and the queue uses it instead of a proxy.

Two facts measured against the real roster on 2026-09-12 shape this module:

- ``procStart`` is formatted in UTC while ``ps -o lstart`` prints local time
  (a 3h skew on this machine). String-comparing the two marks *every* live
  session dead, so there is no start-time guard here — only :func:`pid_alive`.
  PID reuse could in principle call a dead session live; that error is the safe
  direction, and the roster is rewritten by the daemon far faster than a pid
  wraps.
- The roster is a **superset** of the job directories (3 workers, 2 job dirs).
  Absence from a readable roster is therefore real evidence of death; absence
  of the roster itself is not evidence of anything.

Hence the tri-state: ``True`` live, ``False`` dead, ``None`` unknown. Callers
must treat ``None`` as live. A false "dead" hides a session that really is
waiting for a human, which is the precise failure the queue exists to prevent.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def claude_home() -> Path:
    """Where Claude Code keeps daemon state."""
    return Path(os.path.expanduser("~/.claude"))


def read_roster(home: Path | None = None) -> dict[str, int] | None:
    """Map short id -> pid from ``daemon/roster.json``, or ``None`` if unreadable.

    An empty dict and ``None`` mean different things and the distinction is
    load-bearing: ``{}`` is a readable roster saying nothing is running, while
    ``None`` is "we could not ask". Only the first justifies calling a session
    dead.
    """
    base = claude_home() if home is None else home
    try:
        data = json.loads((base / "daemon" / "roster.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    workers = data.get("workers")
    if not isinstance(workers, dict):
        return None

    out: dict[str, int] = {}
    for short_id, worker in workers.items():
        if not isinstance(worker, dict):
            continue
        pid = worker.get("pid")
        # bool is an int subclass; a JSON `true` here is a schema change, not a pid.
        if isinstance(pid, int) and not isinstance(pid, bool):
            out[str(short_id)] = pid
    return out


#: ``PROCESS_QUERY_LIMITED_INFORMATION`` — enough to ask whether a process is
#: running without the right to read or touch it. ``STILL_ACTIVE`` is what
#: ``GetExitCodeProcess`` reports for a process that has not exited.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def _pid_alive_windows(pid: int) -> bool:
    """Ask the Win32 API directly, because ``os.kill`` cannot answer this.

    On Windows ``os.kill(pid, 0)`` is **not** a liveness probe. ``signal 0`` is
    ``CTRL_C_EVENT``, so CPython takes the console-control branch and calls
    ``GenerateConsoleCtrlEvent`` — which, per Win32, "cannot be limited to a
    specific process group": the pid is ignored and every process sharing the
    caller's console gets a real Ctrl-C. It then returns success, so the probe
    also always answers "alive".

    Measured: this killed the test suite on the Windows runner, surfacing as a
    ``KeyboardInterrupt`` in an unrelated test a second or two later (delivery
    is asynchronous), at a different place each run. In production it would
    fire a Ctrl-C at the user's own Claude Code console on every statusline
    render, and `is_abandoned` could never be True.

    A process that has exited but is still held open by a handle reports
    ``STILL_ACTIVE == False`` here, which is the answer the queue wants: the
    session is over.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # ERROR_ACCESS_DENIED (5) means it exists and is not ours to inspect,
        # which is alive for our purposes — the same reading as POSIX EPERM.
        return ctypes.get_last_error() == 5
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def pid_alive(pid: int) -> bool:
    """True when *pid* names a running process.

    Non-positive pids are rejected outright because ``kill(0, 0)`` and
    ``kill(-1, 0)`` address process *groups*, which would answer a question
    nobody asked.

    POSIX asks with signal 0, where ``EPERM`` counts as alive: the process
    exists, it simply is not ours to signal. Windows needs a different call
    entirely — see :func:`_pid_alive_windows`.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def is_live(short_id: str, *, claude_home: Path | None = None,
            roster: dict[str, int] | None = None) -> bool | None:
    """Whether *short_id* has a running process. ``None`` when unknowable.

    Pass *roster* (from :func:`read_roster`) to reuse a single read across a
    whole queue instead of stat-ing the same file once per session.
    """
    if roster is None:
        roster = read_roster(claude_home)
    if roster is None:
        return None  # could not ask; never accuse

    pid = roster.get(short_id)
    if pid is None:
        return False  # readable roster, not listed: the daemon has forgotten it
    return pid_alive(pid)
