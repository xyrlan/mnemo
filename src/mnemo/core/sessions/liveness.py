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
  session dead, so there is no start-time guard here — only ``os.kill(pid, 0)``.
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


def pid_alive(pid: int) -> bool:
    """True when signal 0 reaches *pid*.

    ``EPERM`` counts as alive: the process exists, it simply is not ours to
    signal. Non-positive pids are rejected outright because ``kill(0, 0)`` and
    ``kill(-1, 0)`` address process *groups*, which would answer a question
    nobody asked.
    """
    if pid <= 0:
        return False
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
