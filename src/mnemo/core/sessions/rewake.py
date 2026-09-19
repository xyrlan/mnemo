"""Wake the children a clock has freed, with nobody there to type it (#396).

#393 made a rate-limited child recoverable and #394 shipped ``mnemo resume``.
The recovery still waited for a person who knew to run it: on 2026-09-19 six
children sat stalled for nearly four hours past their reset, and the thing
that noticed was the maintainer asking whether they had finished. This module
is the trigger side, and :mod:`mnemo.cli.commands.resume` still owns the
typed one.

**Why the obvious trigger is not enough, measured.** mnemo's triggers are
hooks, so the first question is whether a hook would ever fire in time.
``tools/measure_wake_latency.py`` answers it over every rate limit on the
maintainer's machine (525 transcripts, 5372 recorded hook events, 15 limits
carrying a reset epoch across 5 distinct resets):

- The first hook event after each reset came **15, 109, 229 and 499 minutes**
  late.
- For **4 of the 5** resets, no hook fired at all between the stall and the
  reset. The 2026-09-19 window is the clearest: eight sessions were stalled at
  once — the six children of #380-#385 and two others — and an idle session
  fires no hook, so nothing ran between 00:43 and 06:29. The 06:29 event that
  ends the gap *is* the manual recovery. **A hook pass would have saved that
  incident nothing.**

The limit that stalls the children silences whatever could notice them. So
the trigger cannot be something that reacts to the stall; it has to be
something that was already running and that needs no API of its own to keep
running. The same measurement says one was available: a hook had fired **9 to
86 minutes before** each of those four stalls.

**Hence two triggers, both in this module.**

- :func:`sweep` — the pass. One bounded, locked, ledgered look at the roster
  that wakes whatever the clock has freed. It is what both triggers run.
- :func:`watch` — a plain Python process, started by SessionStart while there
  is something to watch, that runs :func:`sweep` on a slow tick and exits when
  there is nothing left. It makes no API call, so the account limit cannot
  stop it, and it is the piece that closes the 2026-09-19 gap.

**What it will wake, which is narrower than what ``mnemo resume`` will.**
Only :data:`~mnemo.core.sessions.stalls.Kind.RATE_LIMIT` carrying an actual
``resets_at`` epoch, past that epoch. Three exclusions, each for its own
reason:

- :data:`~mnemo.core.sessions.stalls.Kind.HUMAN` — needs a person; waking it
  replays the failing turn. Never touched, as the issue requires.
- :data:`~mnemo.core.sessions.stalls.Kind.TRANSIENT` — ``overloaded`` and
  ``server_error``, 11 records on this machine. ``is_free`` is True for them
  the moment they are seen, because the wait *is* the retry, and that is a
  fine answer when a person asked for it. It is the wrong one for a trigger:
  there is no epoch, so there is no window, so the ledger below has no key
  that bounds the retry and a child that keeps failing would keep being woken.
  ``mnemo resume`` still wakes them on request.
- A rate limit read from the ``needs`` sentence instead of the transcript. It
  carries no epoch either, for the same reason and with the same consequence.

Put positively: **the trigger acts only on evidence that carries a clock.**

**What bounds re-spending the limit.** ``claude`` reports no remaining quota
(PR #394 checked), so the bound cannot be "when there is room". It is the
reset epoch itself, used as an idempotency key: a child is woken automatically
**at most once per reset window**, recorded in :data:`LEDGER_NAME` against
its full session id. A child that re-stalls immediately gets a *new*
``resets_at`` — the next window boundary, five hours out — so it is eligible
again then and not before. A child that re-stalls inside the same window (a
clock skew, a limit that did not really lift) is not woken again at all. With
the five-hour window that is at most five automatic wakes per child per day,
and the maintainer can still wake one by hand whenever they want.

**What stops a storm (#330).** Three things, all borrowed from
:mod:`mnemo.core.sessions.unblocks`, which is the precedent for a hook that
spawns work:

- :data:`MAX_PER_PASS`, so one pass is bounded however long the roster is.
- a machine-wide lock per vault, so a second concurrent pass is a no-op
  rather than a duplicate. The watcher holds a *second* lock for its whole
  life, so a hundred SessionStarts start one watcher.
- the SessionStart trigger runs under ``hooks_off()`` like every other hook,
  so a ``claude --print`` helper of mnemo's own never reaches it.

**How the maintainer knows it happened.** The ledger, a line in the day log
of the repo the child was working in, and a footer under ``mnemo sessions``.
Not only a line in the child's transcript.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from mnemo.core import locks
from mnemo.core.sessions import stalls as stalls_mod
from mnemo.core.sessions import wake as wake_mod

#: Children one pass wakes. Beyond this the rest wait for the next tick, which
#: is a minute away. Sized like :data:`unblocks.MAX_PER_PASS` and for a
#: different reason: a wake is one fast ``claude --bg --resume`` rather than an
#: LLM round-trip, but it is also the thing that spends the account's window,
#: and a pass that woke thirty children at once would be the 2026-09-19
#: dispatch again with no human in it. Six children is what the incident had;
#: five per tick drains that in two minutes.
MAX_PER_PASS = 5

#: Lock directory under ``<vault>/.mnemo``; one *pass* at a time, machine-wide.
LOCK_NAME = "resume-wake.lock"

#: Lock directory for the watcher; one *process* at a time, machine-wide.
WATCH_LOCK_NAME = "resume-watch.lock"

#: When a held pass lock is assumed to belong to a killed process. A pass is
#: at most :data:`MAX_PER_PASS` subprocess spawns, each capped by
#: :func:`wake.wake`'s own timeout, so a live pass never comes near this.
LOCK_STALE_SECONDS = 10 * 60

#: When a held *watch* lock is assumed to be a killed watcher. The watcher
#: re-stamps its lock every tick, so this only ever elapses after a hard kill
#: — and then the next SessionStart starts a fresh one.
WATCH_LOCK_STALE_SECONDS = 15 * 60

#: Where the wakes are recorded, under ``<vault>/.mnemo``.
LEDGER_NAME = "resume-wakes.json"

#: How often the watcher looks. Nothing needs it faster: the thing it waits
#: for is a clock with minute resolution, and the cost of a look is the
#: ``mnemo resume --dry-run --all`` the issue measured at 0.1 s.
TICK_SECONDS = 60.0

#: The longest a watcher waits with nothing running and nothing to wake.
#: Longer than a ``five_hour`` window, so a watcher that saw a child alive and
#: then saw it stall always outlives the wait that stall created; far shorter
#: than a ``seven_day`` one, which is not worth a week-long process and which
#: ``mnemo resume`` still handles by hand.
#:
#: Measured from the last sign of life, not from the start: a dispatch that
#: runs for eight hours and stalls at the end must not find its watcher
#: already gone. A live session, or a wake, restarts the count.
WATCH_MAX_SECONDS = 6.5 * 3600

#: The longest a watcher lives at all, however busy the machine stays.
#: :data:`WATCH_MAX_SECONDS` restarts on every sign of life, so a machine that
#: never stops dispatching would otherwise keep one process from today's mnemo
#: running next week. A day is long enough that the recycle is invisible and
#: short enough that a watcher is always roughly the version installed: the
#: next SessionStart starts a fresh one, and there is one every few minutes
#: on a machine that is being used.
WATCH_LIFETIME_SECONDS = 24 * 3600

#: Longest single sleep, so a watcher re-reads the wall clock often enough to
#: notice that the machine was suspended for three hours in between.
MAX_SLEEP_SECONDS = 60.0

SCHEMA_VERSION = 1


def enabled(cfg: Optional[Dict[str, Any]]) -> bool:
    """Whether the automatic wake is on. ``resume.auto`` in the config.

    On by default, because the alternative it replaces is the maintainer
    waking six children by hand at six in the morning, and because it grants
    nothing and publishes nothing: the child carries on with the prompt it
    already had. Turn it off if an account window being spent while you are
    not watching is worse than children idling until you are.
    """
    return bool(((cfg or {}).get("resume") or {}).get("auto", True))


def max_per_pass(cfg: Optional[Dict[str, Any]]) -> int:
    """``resume.maxPerPass``, or :data:`MAX_PER_PASS`."""
    try:
        value = int(((cfg or {}).get("resume") or {}).get("maxPerPass", MAX_PER_PASS))
    except (TypeError, ValueError):
        return MAX_PER_PASS
    return value if value > 0 else MAX_PER_PASS


# ---------------------------------------------------------------------------
# the ledger
# ---------------------------------------------------------------------------


def ledger_path(vault_root: Path) -> Path:
    """Where the wakes are recorded for *vault_root*."""
    return Path(vault_root) / ".mnemo" / LEDGER_NAME


def load_ledger(vault_root: Path) -> Dict[str, Any]:
    """The ledger, or an empty one. Never raises: a lost ledger costs a
    duplicate wake, and failing the pass would cost every wake."""
    try:
        data = json.loads(ledger_path(vault_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema_version": SCHEMA_VERSION, "wakes": {}}
    if not isinstance(data, dict) or not isinstance(data.get("wakes"), dict):
        return {"schema_version": SCHEMA_VERSION, "wakes": {}}
    return data


def _save_ledger(vault_root: Path, data: Dict[str, Any]) -> None:
    path = ledger_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def already_woken(ledger: Dict[str, Any], session_id: str, resets_at: int) -> bool:
    """True when this child has already been woken for *this* reset window.

    The whole bound on re-spending the limit, and keyed on the epoch rather
    than on a timestamp or a count because the epoch is the only thing that
    means "a different window". A child woken at 02:41 that stalls again at
    02:45 reports the *next* boundary and is eligible then; one that reports
    02:40 again is not eligible at all.
    """
    entry = (ledger.get("wakes") or {}).get(session_id)
    if not isinstance(entry, dict):
        return False
    try:
        return int(entry.get("resets_at")) == int(resets_at)
    except (TypeError, ValueError):
        return False


def record_wake(
    vault_root: Path, session, stall, *, now: Optional[float] = None
) -> None:
    """Note that *session* was woken for *stall*'s window.

    Re-reads the file rather than writing back a copy read earlier: a pass is
    a handful of subprocess spawns long, and the watcher and a hook pass can
    be recording at the same moment. The pass lock makes that rare, not
    impossible — the lock is per vault and the ledger is the vault's.
    """
    moment = time.time() if now is None else now
    data = load_ledger(vault_root)
    wakes = data.setdefault("wakes", {})
    key = str(getattr(session, "session_id", "") or "")
    if not key:
        return
    previous = wakes.get(key) if isinstance(wakes.get(key), dict) else {}
    wakes[key] = {
        "short_id": getattr(session, "short_id", None),
        "label": getattr(session, "label", None),
        "cwd": getattr(session, "cwd", None),
        "resets_at": None if stall is None else stall.resets_at,
        "limit": None if stall is None else stall.limit,
        "at": datetime.fromtimestamp(moment, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": int(previous.get("count") or 0) + 1,
    }
    data["schema_version"] = SCHEMA_VERSION
    _save_ledger(vault_root, data)


def recent_wakes(vault_root: Path, *, within_seconds: float = 24 * 3600,
                 now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Wakes recorded in the last *within_seconds*, newest first.

    What ``mnemo sessions`` shows the maintainer so the answer to "who woke
    this child" is not "read its transcript".
    """
    moment = time.time() if now is None else now
    out: List[Dict[str, Any]] = []
    for session_id, entry in (load_ledger(vault_root).get("wakes") or {}).items():
        if not isinstance(entry, dict):
            continue
        stamp = _parse_iso(entry.get("at"))
        if stamp is None or moment - stamp > within_seconds:
            continue
        out.append({**entry, "session_id": session_id, "at_epoch": stamp})
    out.sort(key=lambda row: row["at_epoch"], reverse=True)
    return out


def _parse_iso(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# the decision
# ---------------------------------------------------------------------------


def is_auto_wakeable(stall) -> bool:
    """True when *stall* is the one shape a trigger may act on unasked.

    A rate limit that named its own reset epoch, and nothing else. See the
    module docstring for why ``TRANSIENT`` and an epoch-less rate limit are
    both out even though ``mnemo resume`` wakes them.
    """
    return (stall is not None
            and stall.kind == stalls_mod.Kind.RATE_LIMIT
            and stall.resets_at is not None)


def split(sessions: Sequence[Any], stalls: Dict[str, Any], ledger: Dict[str, Any],
          *, now: Optional[float] = None) -> Tuple[List[Tuple[Any, Any]], List[Tuple[Any, Any]]]:
    """``(free now, still waiting)`` among *sessions*, for a trigger.

    ``free`` is what :func:`sweep` would wake: abandoned, clock-named, past
    the reset and not already woken for this window. ``waiting`` is what a
    watcher is waiting *for*: the same thing before its reset. Everything
    else — a human stall, a transient one, a child already woken for this
    window — is in neither, which is what makes "nothing left to watch" a
    decidable question.
    """
    moment = time.time() if now is None else now
    free: List[Tuple[Any, Any]] = []
    waiting: List[Tuple[Any, Any]] = []
    for session in sessions or ():
        if not getattr(session, "is_abandoned", False):
            continue
        stall = stalls.get(getattr(session, "short_id", None))
        if not is_auto_wakeable(stall):
            continue
        if not stall.is_free(now=moment):
            waiting.append((session, stall))
            continue
        session_id = getattr(session, "session_id", "") or ""
        if session_id and already_woken(ledger, session_id, stall.resets_at):
            continue
        free.append((session, stall))
    return free, waiting


def live_count(sessions: Sequence[Any]) -> int:
    """Sessions the roster says are still going.

    Not finished, not blocked, and not *proved* dead: ``live is False`` is the
    daemon roster saying the pid is gone, and a session in that state has
    already had its say through :func:`split`. ``None`` — no roster, nothing
    to ask — counts as going, which is the safe direction for a watcher whose
    only mistake is staying alive a few minutes too long.
    """
    return sum(1 for s in sessions or ()
               if getattr(s, "live", None) is not False
               and not getattr(s, "is_done", False)
               and not getattr(s, "is_blocked", False))


def worth_watching(sessions: Sequence[Any], stalls: Dict[str, Any],
                   ledger: Dict[str, Any], *, now: Optional[float] = None) -> int:
    """How many sessions give a watcher a reason to stay alive.

    A session still running (it may stall later and is the reason a watcher
    was started at all), plus one stalled on a clock that has not come round.
    A machine with neither has nothing for a watcher to do, and the watcher
    exits — which is what keeps this from being a daemon on a machine that
    dispatches nothing.
    """
    moment = time.time() if now is None else now
    free, waiting = split(sessions, stalls, ledger, now=moment)
    return live_count(sessions) + len(free) + len(waiting)


def next_reset(sessions: Sequence[Any], stalls: Dict[str, Any], ledger: Dict[str, Any],
               *, now: Optional[float] = None) -> Optional[int]:
    """The earliest reset a watcher is still waiting on, or ``None``."""
    moment = time.time() if now is None else now
    _, waiting = split(sessions, stalls, ledger, now=moment)
    epochs = [stall.resets_at for _, stall in waiting if stall.resets_at is not None]
    return min(epochs) if epochs else None


# ---------------------------------------------------------------------------
# the pass
# ---------------------------------------------------------------------------


@dataclass
class WakeReport:
    """What one pass did. Plain data, so both triggers can report it."""

    #: Children put back to work.
    woken: List[str] = field(default_factory=list)
    #: Children whose wake was attempted and failed, with why.
    failed: List[str] = field(default_factory=list)
    #: Children whose worktree is gone: nothing to carry on with.
    skipped: List[str] = field(default_factory=list)
    #: Clock-named stalls whose reset has not come round yet.
    waiting: int = 0
    #: Free children this pass did not reach because of its bound.
    remaining: int = 0
    #: True when another pass held the lock, so this one did nothing at all.
    #: Distinct from an empty report, which means there was nothing to do.
    locked: bool = False
    #: True when ``resume.auto`` is off.
    disabled: bool = False

    def did_anything(self) -> bool:
        return bool(self.woken or self.failed or self.skipped)


def sweep(
    cfg: Optional[Dict[str, Any]],
    *,
    vault_root: Path,
    sessions: Optional[Sequence[Any]] = None,
    stalls: Optional[Dict[str, Any]] = None,
    now: Optional[float] = None,
    wake_fn: Optional[Callable[..., Optional[str]]] = None,
    limit: Optional[int] = None,
    announce: bool = True,
) -> WakeReport:
    """Wake every child the clock has freed, at most *limit* of them.

    Never raises: this rides a hook and a detached process, and a roster that
    cannot be read must cost the pass, not the session.

    *sessions* and *stalls* are injected by the tests and by :func:`watch`,
    which has already read them to decide whether to sleep. Given neither,
    the whole machine's roster is read — unscoped on purpose, for the reason
    ``session_end``'s sweep is: the hook fires in one repo and the stalled
    children may be anywhere.
    """
    report = WakeReport()
    if not enabled(cfg):
        report.disabled = True
        return report
    moment = time.time() if now is None else now
    bound = max_per_pass(cfg) if limit is None else limit

    try:
        found = list(sessions) if sessions is not None else _read_sessions()
        stall_map = stalls if stalls is not None else stalls_mod.stalls_for(found)
    except Exception:
        return report

    ledger = load_ledger(vault_root)
    free, waiting = split(found, stall_map, ledger, now=moment)
    report.waiting = len(waiting)
    if not free:
        return report

    with locks.try_lock(_lock_path(vault_root, LOCK_NAME),
                        stale_after=LOCK_STALE_SECONDS) as held:
        if not held:
            report.locked = True
            return report
        # Re-read inside the lock: the pass that held it may have woken these
        # very children, and the ledger is how it said so.
        ledger = load_ledger(vault_root)
        free, _ = split(found, stall_map, ledger, now=moment)
        if bound > 0 and len(free) > bound:
            report.remaining = len(free) - bound
            free = free[:bound]
        for session, stall in free:
            _wake_one(cfg, vault_root, session, stall, report,
                      now=moment, wake_fn=wake_fn or wake_mod.wake,
                      announce=announce)
    return report


def _wake_one(cfg, vault_root: Path, session, stall, report: WakeReport, *,
              now: float, wake_fn, announce: bool) -> None:
    """One child, with the ledger written before the report is believed."""
    label = f"{getattr(session, 'short_id', '?')} {getattr(session, 'label', '')}".strip()
    cwd = getattr(session, "cwd", None)
    if not cwd or not os.path.isdir(cwd):
        # `--resume` would wake the session into a directory that is not
        # there, and there is no uncommitted work left to carry on with.
        report.skipped.append(f"{label} — worktree {cwd} is gone")
        return
    try:
        why = wake_fn(getattr(session, "session_id", "") or "", cwd=cwd)
    except Exception as exc:  # noqa: BLE001 — one child must not take the rest
        why = str(exc) or type(exc).__name__
    if why:
        report.failed.append(f"{label} — {why}")
        return
    report.woken.append(label)
    try:
        record_wake(vault_root, session, stall, now=now)
    except Exception:
        pass
    if announce:
        _announce(cfg, session, stall)


def _announce(cfg, session, stall) -> None:
    """One line in the day log of the repo the child was working in.

    The requirement #396 puts on this is that the maintainer can tell
    afterwards, from something other than the child's transcript. The day log
    is where a session start and a session end already land, so a wake that
    happened at four in the morning reads in sequence with them.
    """
    try:
        from mnemo.core import agent as agent_mod
        from mnemo.core import log_writer

        name = agent_mod.resolve_canonical_agent(getattr(session, "cwd", None) or ".").name
        at = stalls_mod.free_at(stall)
        window = (getattr(stall, "limit", None) or "limit").replace("_", "-")
        tail = f" ({window} reset at {at})" if at else f" ({window} reset)"
        log_writer.append_line(
            name,
            f"⏰ mnemo woke {getattr(session, 'short_id', '?')} "
            f"{getattr(session, 'label', '')}{tail}".strip(),
            cfg or {},
        )
    except Exception:
        pass


def _read_sessions() -> List[Any]:
    from mnemo.core.sessions.jobs import read_sessions

    return list(read_sessions())


def _lock_path(vault_root: Path, name: str) -> Path:
    return Path(vault_root) / ".mnemo" / name


# ---------------------------------------------------------------------------
# the watcher
# ---------------------------------------------------------------------------


def watcher_running(vault_root: Path, *, now: Optional[float] = None) -> bool:
    """True when a watcher is believed to hold its lock right now.

    A ``stat``, so a caller can skip *spawning* a watcher that would only find
    the lock held and exit. Never authoritative — the lock is — and it races
    it harmlessly, exactly as ``unblocks.sweep_in_flight`` does.
    """
    moment = time.time() if now is None else now
    try:
        age = moment - _lock_path(vault_root, WATCH_LOCK_NAME).stat().st_mtime
    except OSError:
        return False
    return age < WATCH_LOCK_STALE_SECONDS


@dataclass
class WatchReport:
    """What one watcher did over its life, for the tests and the log."""

    ticks: int = 0
    woken: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    #: Why it stopped: ``idle`` (nothing left to watch), ``deadline`` (a wait
    #: with no sign of life for :data:`WATCH_MAX_SECONDS`), ``retired`` (old
    #: enough to hand over to a fresh one), ``locked`` (another watcher
    #: already had the job) or ``disabled``.
    stopped: str = "idle"


def watch(
    cfg: Optional[Dict[str, Any]],
    *,
    vault_root: Path,
    clock: Optional[Callable[[], float]] = None,
    sleeper: Optional[Callable[[float], None]] = None,
    wake_fn: Optional[Callable[..., Optional[str]]] = None,
    tick: float = TICK_SECONDS,
    max_seconds: float = WATCH_MAX_SECONDS,
    lifetime: float = WATCH_LIFETIME_SECONDS,
    reader: Optional[Callable[[], List[Any]]] = None,
) -> WatchReport:
    """Run :func:`sweep` on a slow tick until there is nothing left to watch.

    The piece the measurement in the module docstring asks for: a process that
    was started **before** anything stalled, that makes no API call of its own,
    and so is still alive when the account's window reopens at two in the
    morning with every Claude session on the machine dead.

    It is not a daemon, and the difference is that it has an end. It exits as
    soon as :func:`worth_watching` is zero — no session running, nothing
    stalled on a clock still to come — which on a machine that is not running
    dispatched children is the first tick. *max_seconds* is the backstop for
    the case that never becomes true.

    **Why it sleeps in slices.** ``time.sleep`` over a laptop lid closing is
    not a promise about wall-clock, and the thing being waited for is
    wall-clock. Every slice is at most :data:`MAX_SLEEP_SECONDS`, and the
    decision to act is re-taken from ``clock()`` each time, so a machine
    suspended across its own reset wakes the children on the first tick after
    it comes back rather than an hour later.

    *clock*, *sleeper*, *wake_fn* and *reader* are injected by the tests; the
    loop itself has no other state.
    """
    report = WatchReport()
    if not enabled(cfg):
        report.stopped = "disabled"
        return report

    tick_now = clock or time.time
    rest = sleeper or time.sleep
    read = reader or _read_sessions
    #: When the watcher last saw a reason to be here. The idle deadline runs
    #: from this, not from the start — see :data:`WATCH_MAX_SECONDS`.
    alive_at = tick_now()
    #: When this process began. :data:`WATCH_LIFETIME_SECONDS` runs from here
    #: and nothing resets it.
    born_at = alive_at

    lock = _lock_path(vault_root, WATCH_LOCK_NAME)
    with locks.try_lock(lock, stale_after=WATCH_LOCK_STALE_SECONDS) as held:
        if not held:
            # Another watcher has the job. This is the storm guard of #330:
            # every SessionStart may spawn one of these and only the first
            # survives the next line.
            report.stopped = "locked"
            return report
        while True:
            report.ticks += 1
            _touch(lock)
            moment = tick_now()
            try:
                found = read()
                stall_map = stalls_mod.stalls_for(found)
            except Exception:
                found, stall_map = [], {}

            pass_report = sweep(cfg, vault_root=vault_root, sessions=found,
                                stalls=stall_map, now=moment, wake_fn=wake_fn)
            report.woken.extend(pass_report.woken)
            report.failed.extend(pass_report.failed)

            if pass_report.woken:
                # The roster the sleep decision is about has just changed —
                # re-read it rather than deciding from the copy that still
                # calls the children it woke abandoned.
                try:
                    found = read()
                    stall_map = stalls_mod.stalls_for(found)
                except Exception:
                    pass

            ledger = load_ledger(vault_root)
            if not worth_watching(found, stall_map, ledger, now=moment):
                report.stopped = "idle"
                return report
            if moment - born_at >= lifetime:
                # Old enough to be running code the machine no longer has
                # installed. The next SessionStart starts a fresh one, and on
                # a machine busy enough to keep a watcher alive this long,
                # that is minutes away.
                report.stopped = "retired"
                return report
            if pass_report.woken or live_count(found):
                alive_at = moment
            elif moment - alive_at >= max_seconds:
                report.stopped = "deadline"
                return report

            rest(_slice(found, stall_map, ledger, moment, tick,
                        min(alive_at + max_seconds, born_at + lifetime)))


def _slice(found, stall_map, ledger, moment: float, tick: float,
           deadline: float) -> float:
    """How long to sleep before looking again.

    The tick, unless a reset falls sooner — in which case the watcher wakes on
    the reset rather than up to a tick after it — and never past the deadline,
    so the backstop is not overshot by a whole tick. Capped at
    :data:`MAX_SLEEP_SECONDS` so the wall clock is re-read often.
    """
    horizon = min(moment + tick, deadline)
    upcoming = next_reset(found, stall_map, ledger, now=moment)
    if upcoming is not None and moment < upcoming < horizon:
        horizon = float(upcoming)
    return max(0.0, min(MAX_SLEEP_SECONDS, horizon - moment))


def _touch(path: Path) -> None:
    """Re-stamp the watcher's lock so a long wait is not read as a dead one."""
    try:
        os.utime(path, None)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# the SessionStart trigger
# ---------------------------------------------------------------------------


def on_session_start(cfg: Optional[Dict[str, Any]], *, vault_root: Path,
                     spawn: Optional[Callable[[], None]] = None) -> str:
    """Make sure a watcher exists, if this machine has anything to watch.

    Deliberately **not** a wake. The hook must not spawn five
    ``claude --bg --resume`` subprocesses on the path that decides how fast a
    session starts, and it does not have to: the watcher's first tick is the
    pass, and it runs a second later in a process nobody is waiting on.

    Returns what it decided, as a word, for the tests and for ``doctor``:
    ``off``, ``nothing`` (no session worth watching), ``running`` (a watcher
    already holds the lock) or ``spawned``.
    """
    if not enabled(cfg):
        return "off"
    try:
        if watcher_running(vault_root):
            return "running"
        found = _read_sessions()
        ledger = load_ledger(vault_root)
        if not worth_watching(found, stalls_mod.stalls_for(found), ledger):
            return "nothing"
        (spawn or _spawn_watcher)()
    except Exception:
        return "error"
    return "spawned"


def _spawn_watcher() -> None:
    """Fire-and-forget ``mnemo resume --watch``.

    Detached exactly as ``session_end``'s briefing and extraction spawns are,
    and for a stronger reason: this one outlives the session that started it
    on purpose. Its whole value is being alive after everything else is dead.
    """
    import subprocess
    import sys

    from mnemo._selfexec import self_argv

    kwargs: Dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(self_argv("resume", "--watch"), **kwargs)
