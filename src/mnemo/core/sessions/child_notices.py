"""Tell a parent its child stopped, whether or not the child's SessionEnd ran (#502).

Since #357 everything mnemo does when a dispatched child finishes — the
report card to its parent, the ``pr-follow`` registration — hangs off the
child's ``SessionEnd`` hook. On 2026-09-24/25, 95 dispatched children stopped
themselves with ``claude stop``; 27 of their parents were never told, and for
22 of those the hook never reached its own ``session.clear``
(``tools/measure_child_notices.py``). ``.errors.log`` had nothing: the hook
did not fail, it did not run, or was killed before its first line. So the
fix cannot live in the hook. This module is the second channel, and the
hook is left as the fast one.

**What notices the stop.** Claude Code's roster: a child that stopped reads
``state: stopped`` in its ``state.json`` whether any hook ran or not.
:func:`sweep` reads it, and :func:`watch` — a plain Python process that makes
no API call and fires no hook — runs the sweep on a slow tick while any
dispatched child is still running. It is started by every SessionStart
(:func:`on_session_start`; a child's own start included) and by ``mnemo
dispatch`` once it has written the child's parent link, so the watcher exists
before the child can stop.

**What counts as told.** ``child-reports.jsonl``, which the hook's notice
path already writes: a ``spawned`` row when the reporter starts, a
``finished`` row from the reporter or from the hook's own fallback, an
undelivered row when the parent is gone. A row for the child written after
its **last assistant turn** answers that stop. The last turn is the anchor
because nothing the model says can come after its process is gone, while the
hook's row always comes after it; and it separates stops, since a child that
``pr-follow`` woke writes new turns after its first stop's rows.

**Why a child is never told twice.** Order, then a check on both sides. The
sweep leaves a stop alone for :data:`GRACE_SECONDS` after the roster says it
stopped: across the 106 reported stops since 2026-09-15, the hook's first
row came at most 27.4 s after the child's last turn (median 1.6 s,
``tools/measure_child_notices.py``). And the hook, before notifying, looks for a row this module
wrote for the same stop (``via: backstop``) and stands down if there is one:
whichever of the two gets there first, the other sees its row.

**What it does not do.** Brief or extract the child: those also hang off the
hook, and a stop whose hook got past them but not past the notice would be
briefed twice. The briefing still comes from ``mnemo briefing`` run by hand.
Nor does it tell a parent about a child that finished its turn and never
stopped (``done``): that session fires no ``SessionEnd`` either, but it may
still be spoken to, and "finished" would be a claim about a session that is
not over.
"""
from __future__ import annotations

import glob
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from mnemo.core import locks

#: How long a stop is left to its own hook before the sweep acts. The slowest
#: hook row measured came 27.4 s after the child's last turn; two minutes is
#: four times that and still a small delay for a parent.
GRACE_SECONDS = 120.0
#: A report row is stamped to the second; a turn to the millisecond.
SLACK_SECONDS = 2.0
#: Stops older than this are never picked up, whatever the watcher missed: a
#: parent told about a day-old stop is being told about a queue it has read.
LOOKBACK_SECONDS = 24 * 3600.0
#: Notices one pass may start. Each is a detached reporter doing ``gh``
#: calls; a burst of stops is drained over a few ticks, not all at once.
MAX_PER_PASS = 10

TICK_SECONDS = 30.0
#: A watcher with nothing to watch lingers this long before it exits, so a
#: child dispatched a second after the last one finished still has a watcher.
IDLE_SECONDS = 300.0
#: A watcher retires after a day, as ``pr-follow``'s does; the next session
#: start restarts it with whatever mnemo is installed then.
WATCH_LIFETIME_SECONDS = 24 * 3600.0

STATE_NAME = "child-notices.json"
LOCK_NAME = "child-notices.lock"
WATCH_LOCK_NAME = "child-notices-watch.lock"
LOCK_STALE_SECONDS = 5 * 60.0
#: The watcher re-stamps its lock every tick.
WATCH_LOCK_STALE_SECONDS = 5 * 60.0

#: The value this module writes in a report row's ``via``.
VIA = "backstop"

#: Report rows that answer a stop. The reporter's later rows (a checks watch
#: ending) are about a PR, not a stop.
_STOP_EVENTS = frozenset({"spawned", "finished"})


def enabled(cfg: Optional[Dict[str, Any]]) -> bool:
    """The same switch as the hook's notice: ``dispatch.notifyParent``."""
    return bool(((cfg or {}).get("dispatch") or {}).get("notifyParent", False))


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def _epoch(value: Any) -> Optional[float]:
    """An ISO timestamp as Claude Code writes them (``…Z``), as an epoch."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _assistant_turn(raw: bytes) -> Optional[float]:
    if b'"assistant"' not in raw:
        return None
    try:
        record = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(record, dict) or record.get("type") != "assistant":
        return None
    return _epoch(record.get("timestamp"))


def last_turn(transcript: Optional[Path], *, chunk: int = 1 << 16) -> Optional[float]:
    """When the model last spoke in *transcript*, or ``None``.

    Read from the end, a chunk at a time: the answer is almost always in the
    last few records, and a child's transcript runs to megabytes. The hook
    calls this, so it has to stay cheap.
    """
    if not transcript:
        return None
    try:
        with open(transcript, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            pos = fh.tell()
            rest = b""
            while pos > 0:
                step = min(chunk, pos)
                pos -= step
                fh.seek(pos)
                lines = (fh.read(step) + rest).split(b"\n")
                # The first piece may be the tail of a line that starts in
                # the chunk before; it is only whole once that chunk is read.
                rest = lines[0]
                for raw in reversed(lines[1:]):
                    found = _assistant_turn(raw)
                    if found is not None:
                        return found
            return _assistant_turn(rest)
    except OSError:
        return None


def transcript_for(session: Any) -> Optional[Path]:
    """The child's ``.jsonl``: ``state.json``'s own ``linkScanPath`` first,
    then wherever ``~/.claude/projects`` keeps that session id."""
    path = getattr(session, "link_scan_path", None)
    if path and os.path.isfile(path):
        return Path(path)
    sid = getattr(session, "session_id", None)
    if not sid:
        return None
    pattern = os.path.join(os.path.expanduser("~/.claude/projects"), "*", f"{sid}.jsonl")
    found = glob.glob(pattern)
    return Path(found[0]) if found else None


def report_rows(vault_root: Path) -> Dict[str, List[Dict[str, Any]]]:
    """``short_id -> rows`` of ``child-reports.jsonl`` that answer a stop,
    each with its ``ts`` parsed into ``_at``. Never raises."""
    from mnemo.core.sessions import report_card

    out: Dict[str, List[Dict[str, Any]]] = {}
    try:
        text = (Path(vault_root) / ".mnemo" / report_card.LOG_NAME).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return out
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("event") not in _STOP_EVENTS:
            continue
        short_id = row.get("short_id")
        if not isinstance(short_id, str) or not short_id:
            continue
        out.setdefault(short_id, []).append({**row, "_at": report_card._stamp(row)})
    return out


def told(rows: Sequence[Dict[str, Any]], turn: Optional[float], *, via: Optional[str] = None) -> bool:
    """Whether *rows* answer the stop that followed *turn*.

    With no turn to anchor on (no transcript), any row does: the child was
    told about some stop, and a second notice is the worse mistake to risk on
    a guess. *via* narrows to rows written by that channel.
    """
    for row in rows:
        if via is not None and row.get("via") != via:
            continue
        if turn is None:
            return True
        at = row.get("_at")
        if at is not None and at >= turn - SLACK_SECONDS:
            return True
    return False


def told_by_backstop(vault_root: Path, short_id: str, transcript: Any) -> bool:
    """For the hook: did this module already tell the parent about this stop?

    A file read when this module has never written a row for the child, which
    is nearly always; the transcript is read only when it has.
    """
    rows = [r for r in report_rows(vault_root).get(short_id, []) if r.get("via") == VIA]
    if not rows:
        return False
    return told(rows, last_turn(Path(transcript) if transcript else None), via=VIA)


# ---------------------------------------------------------------------------
# what is due
# ---------------------------------------------------------------------------


@dataclass
class Due:
    short_id: str
    session_id: str
    parent: str
    cwd: str
    transcript: Optional[Path]
    stopped_at: float


@dataclass
class Scan:
    due: List[Due] = field(default_factory=list)
    #: Stopped, inside the grace: the hook may still answer them.
    waiting: int = 0
    #: Dispatched children still running (or blocked).
    running: int = 0


def scan(
    sessions: Sequence[Any],
    *,
    links: Dict[str, str],
    rows: Dict[str, List[Dict[str, Any]]],
    now: float,
    armed_at: float,
    find: Callable[[Any], Optional[Path]] = transcript_for,
    turn_of: Callable[[Optional[Path]], Optional[float]] = last_turn,
) -> Scan:
    """Which dispatched children stopped and were never told. Pure but for
    *find* and *turn_of*, which the tests replace."""
    out = Scan()
    for s in sessions:
        short_id = getattr(s, "short_id", None)
        parent = links.get(short_id or "") or getattr(s, "parent_session", None)
        if not short_id or not parent:
            continue
        state = getattr(s, "state", None)
        if state != "stopped":
            if state != "done" and getattr(s, "live", None) is not False:
                out.running += 1
            continue
        stopped_at = _epoch(getattr(s, "updated_at", None))
        if stopped_at is None or stopped_at < armed_at or now - stopped_at > LOOKBACK_SECONDS:
            continue
        if now - stopped_at < GRACE_SECONDS:
            out.waiting += 1
            continue
        mine = rows.get(short_id) or []
        transcript = find(s)
        if mine and told(mine, turn_of(transcript)):
            continue
        out.due.append(Due(
            short_id=short_id, session_id=getattr(s, "session_id", None) or "",
            parent=parent, cwd=getattr(s, "cwd", None) or "", transcript=transcript,
            stopped_at=stopped_at,
        ))
    out.due.sort(key=lambda d: d.stopped_at)
    return out


# ---------------------------------------------------------------------------
# the pass
# ---------------------------------------------------------------------------


def _state_path(vault_root: Path) -> Path:
    return Path(vault_root) / ".mnemo" / STATE_NAME


def _load_state(vault_root: Path) -> Dict[str, Any]:
    try:
        data = json.loads(_state_path(vault_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(vault_root: Path, data: Dict[str, Any]) -> None:
    try:
        path = _state_path(vault_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def armed_at(vault_root: Path, *, now: float) -> float:
    """When this module first ran on the vault, stamping it on the first call.

    Stops from before that were the hook's alone to report; picking them up
    on the first run after an upgrade would send a day of stale notices at
    once. Never raises: an unreadable state reads as "now".
    """
    data = _load_state(vault_root)
    value = data.get("armed_at")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    data["armed_at"] = now
    _save_state(vault_root, data)
    return now


def _mark_sent(vault_root: Path, due: "Due", *, now: float) -> None:
    """Remember that this stop was handled, whatever the row says.

    The report row is what answers a stop, and the notice path always writes
    one; this is the guard for the day it cannot (a full disk, a read-only
    vault), so a stop is tried once rather than every tick. Entries past the
    lookback are dropped, which keeps the file a day's worth of stops.
    """
    data = _load_state(vault_root)
    sent = data.get("sent") if isinstance(data.get("sent"), dict) else {}
    sent = {k: v for k, v in sent.items()
            if isinstance(v, (int, float)) and now - v <= LOOKBACK_SECONDS}
    sent[due.short_id] = due.stopped_at
    data["sent"] = sent
    _save_state(vault_root, data)


def _already_sent(state: Dict[str, Any], due: "Due") -> bool:
    sent = state.get("sent")
    at = sent.get(due.short_id) if isinstance(sent, dict) else None
    return isinstance(at, (int, float)) and at >= due.stopped_at


@dataclass
class PassReport:
    told: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    waiting: int = 0
    running: int = 0
    remaining: int = 0
    locked: bool = False
    disabled: bool = False

    @property
    def watching(self) -> bool:
        """Whether a watcher has a reason to look again. A pass that told
        someone counts: the next dispatch often follows a finish closely."""
        return bool(self.waiting or self.running or self.remaining or self.told)


def _read_sessions() -> List[Any]:
    from mnemo.core.sessions.jobs import read_sessions

    return list(read_sessions())


def _tell(cfg, vault_root: Path, due: Due) -> None:
    """What the hook would have done for this stop: the parent's notice and
    the ``pr-follow`` registration, and a line in the day log saying so."""
    from mnemo.hooks import session_end

    session_end._maybe_notify_parent(
        cfg, vault_root, session_id=due.session_id, cwd=due.cwd,
        transcript=due.transcript, via=VIA,
    )
    try:
        session_end._maybe_follow_pr(cfg, vault_root, session_id=due.session_id, cwd=due.cwd)
    except Exception as exc:  # noqa: BLE001 — the notice already went
        from mnemo.core import errors

        errors.log_error(vault_root, "child_notices.follow_pr", exc)
    try:
        from mnemo.core import agent as agent_mod
        from mnemo.core import log_writer

        name = agent_mod.resolve_canonical_agent(due.cwd or ".").name
        log_writer.append_line(
            name, f"🔕 {due.short_id} stopped without its SessionEnd notice; mnemo told its parent",
            cfg or {},
        )
    except Exception:  # noqa: BLE001
        pass


def sweep(
    cfg: Optional[Dict[str, Any]],
    *,
    vault_root: Path,
    now: Optional[float] = None,
    reader: Optional[Callable[[], List[Any]]] = None,
    tell: Optional[Callable[[Any, Path, Due], None]] = None,
    find: Callable[[Any], Optional[Path]] = transcript_for,
    turn_of: Callable[[Optional[Path]], Optional[float]] = last_turn,
) -> PassReport:
    """Tell the parent of every child that stopped untold. Never raises past
    one child: one that cannot be told must not cost the rest their notice."""
    report = PassReport()
    if not enabled(cfg):
        report.disabled = True
        return report
    from mnemo.core.sessions import parents

    moment = time.time() if now is None else now
    act = tell or _tell
    with locks.try_lock(Path(vault_root) / ".mnemo" / LOCK_NAME,
                        stale_after=LOCK_STALE_SECONDS) as held:
        if not held:
            report.locked = True
            return report
        try:
            sessions = (reader or _read_sessions)()
        except Exception:  # noqa: BLE001
            sessions = []
        found = scan(
            sessions, links=parents.read(vault_root), rows=report_rows(vault_root),
            now=moment, armed_at=armed_at(vault_root, now=moment), find=find, turn_of=turn_of,
        )
        report.waiting, report.running = found.waiting, found.running
        state = _load_state(vault_root)
        for due in found.due:
            if _already_sent(state, due):
                continue
            if len(report.told) + len(report.failed) >= MAX_PER_PASS:
                report.remaining += 1
                continue
            try:
                _mark_sent(vault_root, due, now=moment)
                act(cfg, vault_root, due)
                report.told.append(due.short_id)
            except Exception as exc:  # noqa: BLE001
                report.failed.append(f"{due.short_id} — {exc}")
    return report


# ---------------------------------------------------------------------------
# the watcher
# ---------------------------------------------------------------------------


def _lock(vault_root: Path, name: str) -> Path:
    return Path(vault_root) / ".mnemo" / name


def watcher_running(vault_root: Path, *, now: Optional[float] = None) -> bool:
    """A ``stat``, so a caller can skip spawning a watcher that would exit."""
    moment = time.time() if now is None else now
    try:
        age = moment - _lock(vault_root, WATCH_LOCK_NAME).stat().st_mtime
    except OSError:
        return False
    return age < WATCH_LOCK_STALE_SECONDS


def watch(
    cfg: Optional[Dict[str, Any]],
    *,
    vault_root: Path,
    clock: Optional[Callable[[], float]] = None,
    sleeper: Optional[Callable[[float], None]] = None,
    tick: float = TICK_SECONDS,
    idle: float = IDLE_SECONDS,
    lifetime: float = WATCH_LIFETIME_SECONDS,
    **sweep_kwargs: Any,
) -> str:
    """Run :func:`sweep` every *tick* while a dispatched child could still
    stop untold. Returns why it stopped: ``idle``, ``retired``, ``locked`` or
    ``disabled``."""
    if not enabled(cfg):
        return "disabled"
    now = clock or time.time
    rest = sleeper or time.sleep
    born = now()
    lock = _lock(vault_root, WATCH_LOCK_NAME)
    with locks.try_lock(lock, stale_after=WATCH_LOCK_STALE_SECONDS) as held:
        if not held:
            return "locked"
        busy_at = born
        while True:
            try:
                os.utime(lock, None)
            except OSError:
                pass
            moment = now()
            report = sweep(cfg, vault_root=vault_root, now=moment, **sweep_kwargs)
            if report.watching or report.locked:
                busy_at = moment
            elif moment - busy_at >= idle:
                return "idle"
            if moment - born >= lifetime:
                return "retired"
            rest(tick)


def _spawn_watcher() -> None:
    """``mnemo child-notices``, detached, through the hooks' one chokepoint —
    outside any child's tree, which it would otherwise pin for a day (#506)."""
    from mnemo.hooks.session_start import _spawn_detached, watcher_cwd

    _spawn_detached(["child-notices"], cwd=watcher_cwd())


def ensure_watcher(cfg: Optional[Dict[str, Any]], *, vault_root: Path,
                   spawn: Optional[Callable[[], None]] = None) -> str:
    """Start a watcher unless one is alive. ``off``, ``running`` or
    ``spawned``. A ``stat``; the watcher itself decides whether to stay."""
    if not enabled(cfg):
        return "off"
    if watcher_running(vault_root):
        return "running"
    (spawn or _spawn_watcher)()
    return "spawned"


def on_session_start(cfg: Optional[Dict[str, Any]], *, vault_root: Path,
                     spawn: Optional[Callable[[], None]] = None,
                     reader: Optional[Callable[[], List[Any]]] = None) -> str:
    """Make sure a watcher exists while a dispatched child could stop untold.

    ``off``, ``running``, ``nothing`` or ``spawned``. A roster read and the
    parent links; never a ``gh`` call. A child's own start counts even when
    its ``state.json`` is not written yet, because the dispatcher starts a
    watcher too (:func:`ensure_watcher`, from ``mnemo dispatch``).
    """
    if not enabled(cfg):
        return "off"
    if watcher_running(vault_root):
        return "running"
    from mnemo.core.sessions import parents

    try:
        sessions = (reader or _read_sessions)()
    except Exception:  # noqa: BLE001
        return "nothing"
    moment = time.time()
    # No transcript is read here: a child with any row counts as told, which
    # can only make this say "nothing" where the watcher's sweep would find
    # a stop due — and every dispatched child's own start comes through here
    # while it is running, which is what keeps a watcher alive for it.
    found = scan(
        sessions, links=parents.read(vault_root), rows=report_rows(vault_root),
        now=moment, armed_at=armed_at(vault_root, now=moment),
        find=lambda _s: None, turn_of=lambda _t: None,
    )
    if not (found.running or found.waiting or found.due):
        return "nothing"
    (spawn or _spawn_watcher)()
    return "spawned"
