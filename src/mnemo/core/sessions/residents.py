"""What Claude Code's background daemon is holding in memory right now (#280).

#280 reported a pool of ``claude bg-spare`` processes that dispatch warmed and
nothing retired — 12 processes, 1.19 GB, two of them 1 day 19 hours old. The
diagnosis came from ``ps | grep bg-spare``, and that instrument cannot tell a
spare from a child: **a claimed spare keeps its argv**. The daemon pre-warms a
``bg-pty-host … --bg-spare <x>.claim.sock`` pair, a ``claude --bg`` claims it,
and the running child is still named ``bg-spare`` for its whole life. Measured
on 2026-09-14 against ``daemon.log``, ``daemon/roster.json`` and ``ps``:

- the daemon keeps **one** idle spare. Each of 86 ``claimed-spare`` log lines
  is followed within 5 ms by exactly one ``spare spawned``; three children
  dispatched in 2.5 s left one idle spare behind, not three;
- the "two spares, 3h05, nothing served" (58007/64112) were one spare — host
  and process — and a dispatch claimed it 3h08 in;
- the "1d19h orphans" (13998/14003) were session ``1e5d5558``, a ``done``
  child from the 2026-09-12 dispatch, adopted across a 2.1.269 → 2.1.270
  daemon upgrade and retired by the daemon at 1d19h under ``[low memory]``.

So the resident memory is *workers* — children the daemon keeps alive after
they finish, until they sit idle (``idle 60m`` in most ``bg retire`` lines,
``idle 8h`` on a fresh daemon's sweep; #350) or memory runs low — and the
pool is a constant ~200 MB. mnemo does not retire either: killing the spare makes the
daemon spawn another, and stopping a ``done`` child is the maintainer's call
(``SendMessage`` still reaches it). What mnemo can do is name the split in one
line, which :func:`census` computes and ``mnemo doctor`` prints.

The key is the roster: ``workers[<short id>].pid`` is the *host* pid of a
claimed spare, so a ``--bg-spare`` host absent from it is idle. RSS is summed
over each host's process tree, as ``ps`` reports it — shared pages are counted
once per process, so the totals overstate, in the same way the issue's did.
Everything here reads; nothing signals a process.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

#: The argv marker of a pre-warmed process, claimed or not.
SPARE_MARKER = "--bg-spare"

_ETIME_RE = re.compile(r"^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)$")


def parse_etime(text: str) -> int | None:
    """``ps -o etime`` (``[[dd-]hh:]mm:ss``) → seconds, or ``None``.

    ``etime`` rather than ``etimes`` because macOS ``ps`` has no ``etimes``.
    """
    match = _ETIME_RE.match(text.strip())
    if not match:
        return None
    days, hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    rss_kb: int
    age_s: int
    command: str


def parse_ps(stdout: str) -> dict[int, Proc]:
    """Parse ``ps -eo pid=,ppid=,rss=,etime=,command=``; unparseable lines skip."""
    out: dict[int, Proc] = {}
    for line in (stdout or "").splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        try:
            pid, ppid, rss = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        age = parse_etime(parts[3])
        if age is None:
            continue
        out[pid] = Proc(pid, ppid, rss, age, parts[4])
    return out


def read_ps() -> str | None:
    """The machine's process table as :func:`parse_ps` expects, or ``None``.

    POSIX only: Windows has no ``ps``, and the daemon's process shape there
    was never measured. Never raises.
    """
    if sys.platform == "win32":
        return None
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,rss=,etime=,command="],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def read_roster_raw(home: Path) -> dict[str, Any] | None:
    """``daemon/roster.json`` as a dict, or ``None`` when unreadable."""
    try:
        data = json.loads((home / "daemon" / "roster.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@dataclass(frozen=True)
class Resident:
    """One daemon-owned process tree: an idle spare or a worker."""

    pid: int
    rss_kb: int
    age_s: int
    short_id: str | None = None   # workers only
    state: str | None = None      # workers only; ``None`` when no state.json
    orphaned: bool = False        # spares only: parent is not the live daemon


@dataclass(frozen=True)
class Census:
    spares: tuple[Resident, ...]
    workers: tuple[Resident, ...]

    @property
    def finished(self) -> tuple[Resident, ...]:
        """Workers whose session is ``done`` yet whose process is still resident."""
        return tuple(w for w in self.workers if w.state == "done")


def _tree_rss(pid: int, procs: Mapping[int, Proc], children: Mapping[int, list[int]]) -> int:
    total, stack, seen = 0, [pid], set()
    while stack:
        current = stack.pop()
        if current in seen:  # a pid-reuse cycle must not hang doctor
            continue
        seen.add(current)
        proc = procs.get(current)
        if proc is not None:
            total += proc.rss_kb
        stack.extend(children.get(current, ()))
    return total


def census(procs: Mapping[int, Proc], roster: Mapping[str, Any] | None,
           states: Mapping[str, str | None]) -> Census:
    """Split the daemon's processes into idle spares and resident workers.

    A *root* is a process carrying :data:`SPARE_MARKER` whose parent does not:
    the ``bg-pty-host``. A root whose pid is a roster worker's is that worker;
    any other root is an idle spare. Workers are taken from the roster rather
    than from roots, because a resumed worker's host carries ``--resume`` and
    no spare marker at all. A worker whose pid is not in the process table is
    gone and not counted.
    """
    workers_raw = (roster or {}).get("workers")
    workers_raw = workers_raw if isinstance(workers_raw, dict) else {}
    supervisor = (roster or {}).get("supervisorPid")
    supervisor = supervisor if isinstance(supervisor, int) and not isinstance(supervisor, bool) else None

    children: dict[int, list[int]] = {}
    for proc in procs.values():
        children.setdefault(proc.ppid, []).append(proc.pid)

    worker_pids: dict[int, str] = {}
    for short_id, entry in workers_raw.items():
        pid = entry.get("pid") if isinstance(entry, dict) else None
        if isinstance(pid, int) and not isinstance(pid, bool):
            worker_pids[pid] = str(short_id)

    workers = []
    for pid, short_id in sorted(worker_pids.items(), key=lambda item: item[1]):
        proc = procs.get(pid)
        if proc is None:
            continue
        workers.append(Resident(pid, _tree_rss(pid, procs, children), proc.age_s,
                                short_id=short_id, state=states.get(short_id)))

    spares = []
    for proc in sorted(procs.values(), key=lambda p: p.pid):
        if SPARE_MARKER not in proc.command or proc.pid in worker_pids:
            continue
        parent = procs.get(proc.ppid)
        if parent is not None and SPARE_MARKER in parent.command:
            continue  # the spare's own claude process, counted in its host's tree
        orphaned = supervisor is not None and proc.ppid != supervisor
        spares.append(Resident(proc.pid, _tree_rss(proc.pid, procs, children),
                               proc.age_s, orphaned=orphaned))

    return Census(tuple(spares), tuple(workers))


def format_mb(kb: int) -> str:
    mb = kb / 1024
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def format_age(seconds: int) -> str:
    if seconds >= 86400:
        return f"{seconds // 86400}d{(seconds % 86400) // 3600}h"
    if seconds >= 3600:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    return f"{seconds // 60}m"
