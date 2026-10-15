"""Cross-platform advisory lock built on os.mkdir atomicity."""
from __future__ import annotations

import contextlib
import json
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

#: On Windows a directory another process or thread is removing stays "delete
#: pending" until its last handle closes, and ``mkdir`` on that path fails with
#: access denied (WinError 5), not ``FileExistsError``: the lock is changing
#: hands, which is contention, not an error. A module constant so a test on
#: another platform can take the Windows branch.
DELETE_PENDING_DENIES = os.name == "nt"


def _contended(exc: OSError) -> bool:
    """Whether a failed ``mkdir`` of a lock means someone else has it."""
    return isinstance(exc, PermissionError) and DELETE_PENDING_DENIES


@contextlib.contextmanager
def try_lock(lock_dir: Path, stale_after: float = 60.0) -> Iterator[bool]:
    """Non-blocking advisory lock. Yields True if held, False otherwise.

    Uses os.mkdir as the atomic primitive — works identically on POSIX and
    Windows with no OS-specific imports. Reclaims the lock if the directory
    is older than `stale_after` seconds.
    """
    lock_dir = Path(lock_dir)
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    held = False
    try:
        try:
            os.mkdir(lock_dir)
            held = True
        except FileExistsError:
            try:
                age = time.time() - lock_dir.stat().st_mtime
            except OSError:
                age = 0.0
            if age > stale_after:
                try:
                    os.rmdir(lock_dir)
                    os.mkdir(lock_dir)
                    held = True
                except OSError:
                    held = False
        except PermissionError as exc:
            if not _contended(exc):
                raise
        yield held
    finally:
        if held:
            try:
                os.rmdir(lock_dir)
            except OSError:
                pass


# --- a lock that knows its holder (#674) -----------------------------------
#
# ``try_lock`` judges a lock by its age alone, and nothing refreshes that age,
# so a run longer than ``stale_after`` loses its lock to the next taker while
# it is still going. ``owned_lock`` keeps the same ``mkdir`` primitive and adds
# an ``owner.json`` inside the directory: who holds it (pid, host, a token)
# and, through the file's mtime, a heartbeat a thread refreshes while the lock
# is held. Every reader judges it through :func:`holder`.

OWNER_FILE = "owner.json"

#: How often the holder's thread refreshes ``owner.json``'s mtime.
HEARTBEAT_EVERY = 10.0

#: A heartbeat this old means a holder whose liveness cannot be asked (another
#: host) or a lock left with no owner record (an older mnemo, or the instant
#: between ``mkdir`` and the record's write) is gone: today's 60 s rule.
STALE_AFTER = 60.0

#: A same-host holder whose pid is alive but whose heartbeat stopped this long
#: ago is taken to be a reused pid, not the holder. Only a crash followed by
#: pid reuse, or a holder suspended this long, reaches it.
PID_REUSE_AFTER = 3600.0

#: The takeover lock is held for a re-judgment and an ``rmdir``; one this old
#: belongs to a contender that died mid-takeover.
TAKEOVER_STALE_AFTER = 10.0


@dataclass(frozen=True)
class Holder:
    """What a lock directory says about its holder."""

    alive: bool
    pid: Optional[int]
    host: Optional[str]
    started: Optional[float]
    #: Seconds since the last sign of life: the heartbeat, or the directory's
    #: mtime for a lock with no owner record.
    quiet_for: float
    token: Optional[str]
    reason: str


def _read_owner(lock_dir: Path) -> Optional[dict]:
    try:
        data = json.loads((lock_dir / OWNER_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool):
        return None
    return data


def _mtime(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def holder(lock_dir: Path) -> Optional[Holder]:
    """Judge the lock at *lock_dir*. ``None`` when there is no lock.

    Same host: alive while its pid is, unless the heartbeat stopped
    :data:`PID_REUSE_AFTER` ago. Another host: alive while the heartbeat is
    younger than :data:`STALE_AFTER`. No readable owner record: alive while the
    directory is younger than :data:`STALE_AFTER`, the rule a lock left by an
    older mnemo was always judged by.
    """
    from mnemo.core.sessions.liveness import pid_alive

    lock_dir = Path(lock_dir)
    dir_mtime = _mtime(lock_dir)
    if dir_mtime is None:
        return None
    now = time.time()
    owner = _read_owner(lock_dir)
    if owner is None:
        quiet = max(0.0, now - dir_mtime)
        alive = quiet <= STALE_AFTER
        return Holder(alive, None, None, None, quiet, None,
                      "no owner record" + ("" if alive else f", untouched for {int(quiet)}s"))
    beat = _mtime(lock_dir / OWNER_FILE)
    quiet = max(0.0, now - (beat if beat is not None else dir_mtime))
    pid = owner["pid"]
    host = owner.get("host") if isinstance(owner.get("host"), str) else None
    started = owner.get("started") if isinstance(owner.get("started"), (int, float)) else None
    token = owner.get("token") if isinstance(owner.get("token"), str) else None
    if host == socket.gethostname():
        if not pid_alive(pid):
            alive, reason = False, f"pid {pid} is gone"
        elif quiet > PID_REUSE_AFTER:
            alive, reason = False, f"pid {pid} has not refreshed the lock for {int(quiet)}s"
        else:
            alive, reason = True, f"pid {pid}"
    else:
        alive = quiet <= STALE_AFTER
        reason = f"pid {pid} on {host or 'another host'}" + (
            "" if alive else f", silent for {int(quiet)}s")
    return Holder(alive, pid, host, started, quiet, token, reason)


def _write_owner(lock_dir: Path, token: str) -> None:
    payload = json.dumps({
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started": time.time(),
        "token": token,
    })
    tmp = lock_dir / f".{OWNER_FILE}.{token}.tmp"
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, lock_dir / OWNER_FILE)


def _remove_lock_dir(path: Path) -> None:
    for child in list(path.iterdir()):
        try:
            child.unlink()
        except OSError:
            pass
    os.rmdir(path)


def _take_back(lock_dir: Path, judged: Holder) -> bool:
    """Remove a lock judged dead, unless it changed hands since the judgment.

    Takeovers queue behind a second, short-lived lock beside this one, and the
    holder is judged again under it. A plain ``mkdir`` cannot succeed while the
    dead directory exists, so once the directory judged dead is still the one
    there, nobody else can have made a new one: only this contender removes it.
    """
    with try_lock(lock_dir.with_name(lock_dir.name + ".takeover"),
                  stale_after=TAKEOVER_STALE_AFTER) as serial:
        if not serial:
            return False
        again = holder(lock_dir)
        if again is None or again.alive or again.token != judged.token:
            return False
        try:
            _remove_lock_dir(lock_dir)
        except OSError:
            return False
        return True


def _heartbeat(owner: Path, stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_EVERY):
        try:
            os.utime(owner, None)
        except OSError:
            return


@contextlib.contextmanager
def owned_lock(lock_dir: Path) -> Iterator[bool]:
    """Non-blocking lock that stays held for as long as its holder runs.

    Yields True if held. A lock whose holder :func:`holder` judges dead is
    taken back; a live one is never taken, however long it has been held. On
    the way out the lock is removed only if it is still this holder's.
    """
    lock_dir = Path(lock_dir)
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    held = False
    stop = threading.Event()
    beat: Optional[threading.Thread] = None
    try:
        try:
            os.mkdir(lock_dir)
            held = True
        except FileExistsError:
            judged = holder(lock_dir)
            if judged is not None and not judged.alive and _take_back(lock_dir, judged):
                try:
                    os.mkdir(lock_dir)
                    held = True
                except OSError:
                    held = False
        except PermissionError as exc:
            if not _contended(exc):
                raise
        if held:
            try:
                _write_owner(lock_dir, token)
            except OSError:
                # Held all the same: with no record it is judged by its age,
                # as every lock was before #674.
                pass
            else:
                beat = threading.Thread(
                    target=_heartbeat, args=(lock_dir / OWNER_FILE, stop),
                    name="mnemo-lock-heartbeat", daemon=True,
                )
                beat.start()
        yield held
    finally:
        stop.set()
        if beat is not None:
            beat.join(timeout=HEARTBEAT_EVERY)
        if held:
            current = _read_owner(lock_dir)
            if beat is None or (current is not None and current.get("token") == token):
                try:
                    _remove_lock_dir(lock_dir)
                except OSError:
                    pass
