"""The second channel that tells a parent its child stopped (#502).

The roster is a reader, the notice is a recorder, and the transcript is a
file written here in Claude Code's shape. Nothing starts a process.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import pytest

from mnemo.core.sessions import child_notices as cn
from mnemo.core.sessions import parents, report_card

FULL = "594436f2-a282-4a1c-b04a-08441296047e"
SHORT = FULL[:8]
PARENT = "0ff9d810-e54d-41f3-a045-b0ccff6c5186"
T0 = 1_790_000_000.0
ON = {"dispatch": {"notifyParent": True}}


def _iso(epoch: float) -> str:
    ms = int(round((epoch % 1) * 1000))
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch)) + f".{ms:03d}Z"


@dataclass
class S:
    """A roster row: the fields the scan reads."""

    short_id: str = SHORT
    session_id: str = FULL
    state: str = "stopped"
    updated_at: Optional[str] = None
    cwd: str = "/x/app-wt-7"
    live: Optional[bool] = False
    link_scan_path: Optional[str] = None
    parent_session: Optional[str] = None


def _transcript(tmp_path: Path, turns: List[float], *, tail: int = 0) -> Path:
    """Assistant turns at *turns*, then *tail* non-assistant records."""
    path = tmp_path / f"{FULL}.jsonl"
    lines = []
    for at in turns:
        lines.append({"type": "user", "timestamp": _iso(at - 1), "message": {"content": "go"}})
        lines.append({"type": "assistant", "timestamp": _iso(at),
                      "message": {"content": [{"type": "text", "text": "done"}]}})
    for i in range(tail):
        lines.append({"type": "cost-state", "n": i})
    path.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
    return path


def _link(vault: Path, short_id: str = SHORT, parent: str = PARENT) -> None:
    path = parents.log_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"short_id": short_id, "parent_session": parent}) + "\n")


def _row(vault: Path, at: float, **extra) -> None:
    """A report row stamped *at*, as ``report_card.record`` stamps it."""
    path = vault / ".mnemo" / report_card.LOG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(at)),
           "short_id": SHORT, "parent": PARENT, "event": "spawned", **extra}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


# --- the last turn ----------------------------------------------------------


def test_last_turn_is_the_last_assistant_record_not_the_last_record(tmp_path):
    path = _transcript(tmp_path, [T0, T0 + 50], tail=3)
    assert cn.last_turn(path) == pytest.approx(T0 + 50, abs=0.01)


def test_last_turn_reads_across_chunk_boundaries(tmp_path):
    """The chunk that holds the turn also holds the start of a line cut by the
    chunk after it; a small chunk puts that seam everywhere."""
    path = _transcript(tmp_path, [T0, T0 + 7], tail=40)
    for chunk in (7, 64, 333):
        assert cn.last_turn(path, chunk=chunk) == pytest.approx(T0 + 7, abs=0.01)


def test_last_turn_of_nothing(tmp_path):
    assert cn.last_turn(None) is None
    assert cn.last_turn(tmp_path / "missing.jsonl") is None
    empty = tmp_path / "e.jsonl"
    empty.write_text("", encoding="utf-8")
    assert cn.last_turn(empty) is None


# --- what is due ------------------------------------------------------------


def _scan(sessions, vault: Path, *, now: float, transcript: Optional[Path] = None,
          armed_at: float = 0.0):
    return cn.scan(
        sessions, links=parents.read(vault), rows=cn.report_rows(vault), now=now,
        armed_at=armed_at, find=lambda _s: transcript,
    )


def test_a_stop_with_no_row_is_due_once_the_grace_is_over(tmp_vault, tmp_path):
    _link(tmp_vault)
    stop = T0 + 10
    transcript = _transcript(tmp_path, [T0])
    s = S(updated_at=_iso(stop))

    early = _scan([s], tmp_vault, now=stop + cn.GRACE_SECONDS - 1, transcript=transcript)
    assert early.due == [] and early.waiting == 1

    late = _scan([s], tmp_vault, now=stop + cn.GRACE_SECONDS + 1, transcript=transcript)
    assert [d.short_id for d in late.due] == [SHORT]
    assert late.due[0].parent == PARENT
    assert late.due[0].session_id == FULL
    assert late.due[0].transcript == transcript


def test_a_row_after_the_last_turn_answers_the_stop(tmp_vault, tmp_path):
    _link(tmp_vault)
    _row(tmp_vault, T0 + 3)
    found = _scan([S(updated_at=_iso(T0 + 5))], tmp_vault, now=T0 + 1000,
                  transcript=_transcript(tmp_path, [T0]))
    assert found.due == []


def test_a_row_from_an_earlier_stop_does_not_answer_a_later_one(tmp_vault, tmp_path):
    """pr-follow woke the child after its first stop, the child worked, and
    its second stop's hook never ran: the old row is not an answer."""
    _link(tmp_vault)
    _row(tmp_vault, T0 + 3)
    found = _scan([S(updated_at=_iso(T0 + 905))], tmp_vault, now=T0 + 2000,
                  transcript=_transcript(tmp_path, [T0, T0 + 900]))
    assert [d.short_id for d in found.due] == [SHORT]


def test_a_row_stamped_to_the_second_still_answers_a_turn_in_the_same_second(tmp_vault, tmp_path):
    _link(tmp_vault)
    _row(tmp_vault, T0)  # the row's ts truncates T0 + 0.9 to T0
    found = _scan([S(updated_at=_iso(T0 + 2))], tmp_vault, now=T0 + 1000,
                  transcript=_transcript(tmp_path, [T0 + 0.9]))
    assert found.due == []


def test_without_a_transcript_any_row_counts_as_told(tmp_vault):
    _link(tmp_vault)
    _row(tmp_vault, T0 - 5000)
    found = _scan([S(updated_at=_iso(T0))], tmp_vault, now=T0 + 1000, transcript=None)
    assert found.due == []


def test_rows_about_checks_do_not_answer_a_stop(tmp_vault, tmp_path):
    _link(tmp_vault)
    _row(tmp_vault, T0 + 30, event="settled")
    found = _scan([S(updated_at=_iso(T0 + 5))], tmp_vault, now=T0 + 1000,
                  transcript=_transcript(tmp_path, [T0]))
    assert [d.short_id for d in found.due] == [SHORT]


@pytest.mark.parametrize("session", [
    S(short_id="aaaaaaaa", session_id="aaaaaaaa-1", updated_at=_iso(T0)),  # nobody dispatched it
    S(state="done", updated_at=_iso(T0)),                                   # finished, not stopped
    S(updated_at=None),                                                     # no stop time
])
def test_what_is_never_due(tmp_vault, session):
    _link(tmp_vault)
    assert _scan([session], tmp_vault, now=T0 + 1000).due == []


def test_stops_before_arming_or_past_the_lookback_are_left_alone(tmp_vault):
    _link(tmp_vault)
    s = S(updated_at=_iso(T0))
    assert _scan([s], tmp_vault, now=T0 + 1000, armed_at=T0 + 1).due == []
    assert _scan([s], tmp_vault, now=T0 + cn.LOOKBACK_SECONDS + 1).due == []


def test_running_children_are_counted_for_the_watcher(tmp_vault):
    _link(tmp_vault)
    _link(tmp_vault, "bbbbbbbb")
    _link(tmp_vault, "cccccccc")
    found = _scan([
        S(state="working", live=True),
        S(short_id="bbbbbbbb", state="blocked", live=None),
        S(short_id="cccccccc", state="working", live=False),  # provably dead
    ], tmp_vault, now=T0)
    assert found.running == 2


# --- the pass ---------------------------------------------------------------


def _sweep(vault: Path, sessions, *, now: float, transcript=None, cfg=ON):
    told = []
    report = cn.sweep(
        cfg, vault_root=vault, now=now, reader=lambda: sessions,
        tell=lambda c, v, due: told.append(due), find=lambda _s: transcript,
    )
    return report, told


def test_sweep_tells_a_silent_stop(tmp_vault, tmp_path):
    _link(tmp_vault)
    cn.armed_at(tmp_vault, now=T0 - 10_000)
    report, told = _sweep(tmp_vault, [S(updated_at=_iso(T0))], now=T0 + 500,
                          transcript=_transcript(tmp_path, [T0 - 3]))
    assert report.told == [SHORT]
    assert [d.cwd for d in told] == ["/x/app-wt-7"]


def test_sweep_arms_on_its_first_run_and_tells_nothing_from_before(tmp_vault):
    _link(tmp_vault)
    report, told = _sweep(tmp_vault, [S(updated_at=_iso(T0))], now=T0 + 500)
    assert told == [] and report.told == []
    assert json.loads((tmp_vault / ".mnemo" / cn.STATE_NAME).read_text(encoding="utf-8"))["armed_at"] == T0 + 500


def test_sweep_is_off_with_the_hooks_notice(tmp_vault):
    report, told = _sweep(tmp_vault, [S(updated_at=_iso(T0))], now=T0 + 500,
                          cfg={"dispatch": {"notifyParent": False}})
    assert report.disabled and told == []


def test_sweep_is_bounded_per_pass(tmp_vault):
    cn.armed_at(tmp_vault, now=0)
    sessions = []
    for i in range(cn.MAX_PER_PASS + 3):
        sid = f"{i:08x}"
        _link(tmp_vault, sid)
        sessions.append(S(short_id=sid, session_id=f"{sid}-x", updated_at=_iso(T0 + i)))
    report, told = _sweep(tmp_vault, sessions, now=T0 + 1000)
    assert len(told) == cn.MAX_PER_PASS
    assert report.remaining == 3
    assert report.watching


def test_one_failing_notice_does_not_cost_the_others(tmp_vault):
    cn.armed_at(tmp_vault, now=0)
    _link(tmp_vault, "aaaaaaaa")
    _link(tmp_vault, "bbbbbbbb")
    sessions = [S(short_id="aaaaaaaa", updated_at=_iso(T0)),
                S(short_id="bbbbbbbb", updated_at=_iso(T0 + 1))]

    def tell(cfg, vault, due):
        if due.short_id == "aaaaaaaa":
            raise RuntimeError("boom")

    report = cn.sweep(ON, vault_root=tmp_vault, now=T0 + 1000, reader=lambda: sessions,
                      tell=tell, find=lambda _s: None)
    assert report.told == ["bbbbbbbb"]
    assert report.failed and report.failed[0].startswith("aaaaaaaa")


def test_a_stop_is_tried_once_even_when_no_row_lands(tmp_vault):
    """The notice path always writes a row; if the vault cannot take one, the
    sweep's own ledger still keeps a watcher from re-telling every tick."""
    cn.armed_at(tmp_vault, now=0)
    _link(tmp_vault)
    sessions = [S(updated_at=_iso(T0))]
    first, told = _sweep(tmp_vault, sessions, now=T0 + 500)
    second, told_again = _sweep(tmp_vault, sessions, now=T0 + 530)
    assert first.told == [SHORT] and told_again == []

    # A later stop of the same child is a new stop.
    later = [S(updated_at=_iso(T0 + 2000))]
    third, told_later = _sweep(tmp_vault, later, now=T0 + 2500)
    assert third.told == [SHORT]


def test_sweep_tells_through_the_hooks_own_notice_and_marks_the_row(tmp_vault, tmp_path, monkeypatch):
    """The backstop is the hook's notice path, run from outside the hook: same
    reporter, same rows, plus ``via`` — and the stop is then answered."""
    from mnemo.core.sessions import inbox
    from mnemo.hooks import session_end

    cn.armed_at(tmp_vault, now=0)
    _link(tmp_vault)
    monkeypatch.setattr(inbox, "resolve", lambda v, p: ({"socket": "/s"}, ""))
    started = []
    monkeypatch.setattr(session_end, "_spawn_detached_child_report",
                        lambda short_id, **kw: started.append((short_id, kw)) or 4242)
    followed = []
    monkeypatch.setattr(session_end, "_maybe_follow_pr",
                        lambda cfg, vault, **kw: followed.append(kw))
    transcript = _transcript(tmp_path, [T0 - 3])
    sessions = [S(updated_at=_iso(T0))]

    report = cn.sweep(ON, vault_root=tmp_vault, now=T0 + 500, reader=lambda: sessions,
                      find=lambda _s: transcript)

    assert report.told == [SHORT]
    assert started == [(SHORT, {"parent": PARENT, "cwd": "/x/app-wt-7", "transcript": transcript})]
    assert followed == [{"session_id": FULL, "cwd": "/x/app-wt-7"}]
    rows = [json.loads(l) for l in (tmp_vault / ".mnemo" / report_card.LOG_NAME).read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["event"] == "spawned" and rows[-1]["via"] == cn.VIA and rows[-1]["pid"] == 4242

    # The next pass finds it answered.
    again = cn.sweep(ON, vault_root=tmp_vault, now=T0 + 600, reader=lambda: sessions,
                     find=lambda _s: transcript)
    assert again.told == []


# --- the hook's side --------------------------------------------------------


def test_told_by_backstop_reads_only_its_own_rows_for_this_stop(tmp_vault, tmp_path):
    transcript = _transcript(tmp_path, [T0, T0 + 900])
    assert not cn.told_by_backstop(tmp_vault, SHORT, transcript)
    _row(tmp_vault, T0 + 950)  # the hook's own row: not the backstop's
    assert not cn.told_by_backstop(tmp_vault, SHORT, transcript)
    _row(tmp_vault, T0 + 100, via=cn.VIA)  # the backstop, about the first stop
    assert not cn.told_by_backstop(tmp_vault, SHORT, transcript)
    _row(tmp_vault, T0 + 1030, via=cn.VIA)  # the backstop, about this stop
    assert cn.told_by_backstop(tmp_vault, SHORT, str(transcript))


# --- the watcher ------------------------------------------------------------


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def test_watcher_stays_while_a_child_runs_and_leaves_once_idle(tmp_vault):
    cn.armed_at(tmp_vault, now=0)
    _link(tmp_vault)
    clock = Clock(T0)
    told = []
    # Running for five minutes, stopped at T0+300, told after the grace.
    def roster():
        if clock.t < T0 + 300:
            return [S(state="working", live=True)]
        return [S(updated_at=_iso(T0 + 300))]

    why = cn.watch(ON, vault_root=tmp_vault, clock=clock, sleeper=clock.sleep, tick=30,
                   idle=120, reader=roster, tell=lambda c, v, d: told.append(d.short_id),
                   find=lambda _s: None)
    assert why == "idle"
    assert told == [SHORT]
    # Grace after the stop, then the idle wait after it was told.
    assert T0 + 300 + cn.GRACE_SECONDS + 120 <= clock.t <= T0 + 300 + cn.GRACE_SECONDS + 120 + 60


def test_a_second_watcher_exits_on_the_lock(tmp_vault):
    from mnemo.core import locks

    with locks.try_lock(tmp_vault / ".mnemo" / cn.WATCH_LOCK_NAME) as held:
        assert held
        assert cn.watch(ON, vault_root=tmp_vault, reader=lambda: []) == "locked"


def test_watcher_retires(tmp_vault):
    clock = Clock(T0)
    _link(tmp_vault)
    why = cn.watch(ON, vault_root=tmp_vault, clock=clock, sleeper=clock.sleep, tick=60,
                   lifetime=600, reader=lambda: [S(state="working", live=True)],
                   tell=lambda *a: None)
    assert why == "retired"


def test_session_start_spawns_a_watcher_only_when_a_child_could_stop(tmp_vault):
    spawned = []
    assert cn.on_session_start({}, vault_root=tmp_vault, spawn=lambda: spawned.append(1)) == "off"
    assert cn.on_session_start(ON, vault_root=tmp_vault, reader=lambda: [],
                               spawn=lambda: spawned.append(1)) == "nothing"
    _link(tmp_vault)
    assert cn.on_session_start(ON, vault_root=tmp_vault,
                               reader=lambda: [S(state="working", live=True)],
                               spawn=lambda: spawned.append(1)) == "spawned"
    assert spawned == [1]


def test_session_start_leaves_a_live_watcher_alone(tmp_vault):
    lock = tmp_vault / ".mnemo" / cn.WATCH_LOCK_NAME
    lock.mkdir(parents=True)
    assert cn.on_session_start(ON, vault_root=tmp_vault,
                               spawn=lambda: pytest.fail("one is alive")) == "running"
    assert cn.ensure_watcher(ON, vault_root=tmp_vault,
                             spawn=lambda: pytest.fail("one is alive")) == "running"


def test_ensure_watcher_goes_through_the_hooks_chokepoint(tmp_vault, monkeypatch):
    from mnemo.hooks import session_start

    seen = []
    monkeypatch.setattr(session_start, "_spawn_detached", lambda args, cwd=None: seen.append(args))
    assert cn.ensure_watcher(ON, vault_root=tmp_vault) == "spawned"
    assert seen == [["child-notices"]]


def test_sweep_tells_the_same_stop_from_the_watchers_cwd(tmp_vault, tmp_path, monkeypatch):
    """#506: the watcher runs in the vault; the stop it tells carries the
    child's own tree, never the watcher's cwd."""
    monkeypatch.chdir(tmp_vault)
    _link(tmp_vault)
    cn.armed_at(tmp_vault, now=T0 - 10_000)
    report, told = _sweep(tmp_vault, [S(updated_at=_iso(T0))], now=T0 + 500,
                          transcript=_transcript(tmp_path, [T0 - 3]))
    assert report.told == [SHORT]
    assert [d.cwd for d in told] == ["/x/app-wt-7"]
