"""The automatic wake: what it acts on, what it refuses, what bounds it (#396).

``subprocess`` is never reached — :func:`mnemo.core.sessions.wake.wake` is
replaced throughout — so every assertion here is about the decision. The wake
itself is measured in ``claude_cli``'s ``resume-under-own-id``, and the
end-to-end read of a real stalled child is
:func:`test_wakes_the_real_stalled_child_from_its_transcript`, which builds
its stall out of the transcript PR #394 took from one.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from mnemo.core.sessions import rewake
from mnemo.core.sessions.jobs import Session
from mnemo.core.sessions.stalls import Kind, Stall

NOW = 1_789_800_000.0
RESET = int(NOW) - 600            # ten minutes ago: free
NEXT_RESET = int(NOW) + 18_000    # five hours out: the window after this one
LATER = int(NOW) + 3_600          # an hour out: still waiting

FREE = Stall(kind=Kind.RATE_LIMIT, error="rate_limit", resets_at=RESET,
             limit="five_hour")
WAITING = Stall(kind=Kind.RATE_LIMIT, error="rate_limit", resets_at=LATER,
                limit="five_hour")
#: A rate limit with no epoch: the per-model credit cap, and what
#: ``from_needs`` produces. ``mnemo resume`` wakes it; a trigger must not.
NO_CLOCK = Stall(kind=Kind.RATE_LIMIT, error="rate_limit", resets_at=None)
TRANSIENT = Stall(kind=Kind.TRANSIENT, error="overloaded")
HUMAN = Stall(kind=Kind.HUMAN, error="authentication_failed")

CFG: Dict[str, Any] = {"resume": {"auto": True, "maxPerPass": 5}}

#: Bound at import, before ``bench`` stubs it out, so the one test that wants
#: the real day-log write can put it back without undoing the autouse fixtures
#: that share this test's ``monkeypatch``.
_REAL_ANNOUNCE = rewake._announce


def child(short_id: str, tree: Path, *, live: bool = False,
          state: str = "blocked", tempo: str = "blocked") -> Session:
    return Session(short_id=short_id, state=state, tempo=tempo,
                   needs="rate limited — wait and retry", cwd=str(tree),
                   live=live,
                   session_id=f"{short_id}-a282-4a1c-b04a-08441296047e",
                   updated_at="2026-09-19T03:51:57Z")


@pytest.fixture()
def bench(tmp_path, monkeypatch):
    """A vault, two children with real trees, and a recording wake."""
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    trees = {}
    for issue in (380, 381):
        tree = tmp_path / f"mnemo-wt-{issue}"
        tree.mkdir()
        trees[issue] = tree

    woken: List[str] = []

    def _wake(session_id, *, cwd, **kwargs):
        woken.append(session_id)
        return None

    # The day-log line is real behaviour, not the thing under test here, and
    # it resolves an agent off a tmp path. Silenced so a failure to write it
    # cannot read as a failure to wake.
    monkeypatch.setattr(rewake, "_announce", lambda *a, **k: None)
    return {"vault": vault, "trees": trees, "woken": woken, "wake": _wake}


# ---------------------------------------------------------------------------
# what it acts on
# ---------------------------------------------------------------------------


def test_wakes_a_child_whose_reset_has_passed(bench):
    sessions = [child("594436f2", bench["trees"][380])]
    report = rewake.sweep(CFG, vault_root=bench["vault"], sessions=sessions,
                          stalls={"594436f2": FREE}, now=NOW,
                          wake_fn=bench["wake"])
    assert len(report.woken) == 1
    assert bench["woken"] == ["594436f2-a282-4a1c-b04a-08441296047e"]


def test_waits_for_a_reset_still_to_come(bench):
    sessions = [child("594436f2", bench["trees"][380])]
    report = rewake.sweep(CFG, vault_root=bench["vault"], sessions=sessions,
                          stalls={"594436f2": WAITING}, now=NOW,
                          wake_fn=bench["wake"])
    assert report.woken == [] and report.waiting == 1
    assert bench["woken"] == []


@pytest.mark.parametrize("stall", [HUMAN, TRANSIENT, NO_CLOCK, None],
                         ids=["human", "transient", "no-epoch", "unreadable"])
def test_never_wakes_a_stall_without_a_clock(bench, stall):
    """The trigger acts only on evidence that carries a clock.

    ``HUMAN`` needs a person. ``TRANSIENT`` and an epoch-less rate limit are
    both *free* to ``mnemo resume`` — ``is_free`` is True for them — and are
    still refused here, because with no epoch the ledger has no key and
    nothing would bound a child that keeps failing.
    """
    sessions = [child("594436f2", bench["trees"][380])]
    stalls = {} if stall is None else {"594436f2": stall}
    report = rewake.sweep(CFG, vault_root=bench["vault"], sessions=sessions,
                          stalls=stalls, now=NOW, wake_fn=bench["wake"])
    assert report.woken == [] and bench["woken"] == []


def test_never_wakes_a_session_that_is_still_running(bench):
    """``--resume`` on a live session starts a *copy* in the same worktree."""
    alive = child("594436f2", bench["trees"][380], live=True)
    report = rewake.sweep(CFG, vault_root=bench["vault"], sessions=[alive],
                          stalls={"594436f2": FREE}, now=NOW,
                          wake_fn=bench["wake"])
    assert report.woken == [] and bench["woken"] == []


def test_skips_a_child_whose_worktree_is_gone(bench):
    gone = child("594436f2", bench["trees"][380] / "removed")
    report = rewake.sweep(CFG, vault_root=bench["vault"], sessions=[gone],
                          stalls={"594436f2": FREE}, now=NOW,
                          wake_fn=bench["wake"])
    assert report.woken == [] and len(report.skipped) == 1
    assert bench["woken"] == []


def test_off_in_config_wakes_nothing(bench):
    sessions = [child("594436f2", bench["trees"][380])]
    report = rewake.sweep({"resume": {"auto": False}}, vault_root=bench["vault"],
                          sessions=sessions, stalls={"594436f2": FREE}, now=NOW,
                          wake_fn=bench["wake"])
    assert report.disabled and bench["woken"] == []


def test_a_failed_wake_does_not_take_the_others_with_it(bench):
    sessions = [child("594436f2", bench["trees"][380]),
                child("aaaaaaaa", bench["trees"][381])]

    def _wake(session_id, *, cwd, **kwargs):
        if session_id.startswith("594436f2"):
            return "boom"
        bench["woken"].append(session_id)
        return None

    report = rewake.sweep(CFG, vault_root=bench["vault"], sessions=sessions,
                          stalls={"594436f2": FREE, "aaaaaaaa": FREE}, now=NOW,
                          wake_fn=_wake)
    assert len(report.failed) == 1 and len(report.woken) == 1


# ---------------------------------------------------------------------------
# the bound on re-spending the limit
# ---------------------------------------------------------------------------


def test_a_child_that_re_stalls_in_the_same_window_is_not_woken_again(bench):
    """The bound #396 asks for, stated as a test.

    The same child, stalled again on the same reset epoch — a clock skew, or a
    limit that did not really lift. The ledger key is ``(session, epoch)``, so
    the second pass does nothing at all.
    """
    sessions = [child("594436f2", bench["trees"][380])]
    stalls = {"594436f2": FREE}
    first = rewake.sweep(CFG, vault_root=bench["vault"], sessions=sessions,
                         stalls=stalls, now=NOW, wake_fn=bench["wake"])
    second = rewake.sweep(CFG, vault_root=bench["vault"], sessions=sessions,
                          stalls=stalls, now=NOW + 60, wake_fn=bench["wake"])
    third = rewake.sweep(CFG, vault_root=bench["vault"], sessions=sessions,
                         stalls=stalls, now=NOW + 4 * 3600, wake_fn=bench["wake"])
    assert len(first.woken) == 1
    assert second.woken == [] and third.woken == []
    assert len(bench["woken"]) == 1


def test_the_next_window_makes_it_eligible_again(bench):
    """One wake per reset window, not one wake ever.

    A child woken at the reset that immediately burns the fresh window reports
    the *next* boundary. That is a different key, so it is woken once more when
    that one comes round — and with a five-hour window, at most five times a
    day.
    """
    sessions = [child("594436f2", bench["trees"][380])]
    rewake.sweep(CFG, vault_root=bench["vault"], sessions=sessions,
                 stalls={"594436f2": FREE}, now=NOW, wake_fn=bench["wake"])
    again = Stall(kind=Kind.RATE_LIMIT, error="rate_limit",
                  resets_at=NEXT_RESET, limit="five_hour")
    report = rewake.sweep(CFG, vault_root=bench["vault"], sessions=sessions,
                          stalls={"594436f2": again}, now=NEXT_RESET + 1,
                          wake_fn=bench["wake"])
    assert len(report.woken) == 1 and len(bench["woken"]) == 2


def test_the_ledger_says_who_was_woken_and_when(bench):
    sessions = [child("594436f2", bench["trees"][380])]
    rewake.sweep(CFG, vault_root=bench["vault"], sessions=sessions,
                 stalls={"594436f2": FREE}, now=NOW, wake_fn=bench["wake"])
    rows = rewake.recent_wakes(bench["vault"], now=NOW + 60)
    assert len(rows) == 1
    assert rows[0]["short_id"] == "594436f2"
    assert rows[0]["resets_at"] == RESET
    assert rows[0]["limit"] == "five_hour"
    assert rows[0]["count"] == 1
    # And it ages out, so the footer never shows last week's dispatch.
    assert rewake.recent_wakes(bench["vault"], now=NOW + 2 * 86400) == []


def test_a_pass_is_bounded(bench, tmp_path):
    """Five per pass, however long the roster is. The rest wait a tick."""
    sessions, stalls = [], {}
    for n in range(8):
        tree = tmp_path / f"tree-{n}"
        tree.mkdir()
        short = f"0000000{n}"
        sessions.append(child(short, tree))
        stalls[short] = FREE
    report = rewake.sweep(CFG, vault_root=bench["vault"], sessions=sessions,
                          stalls=stalls, now=NOW, wake_fn=bench["wake"])
    assert len(report.woken) == 5 and report.remaining == 3


# ---------------------------------------------------------------------------
# concurrency (#330)
# ---------------------------------------------------------------------------


def test_a_second_concurrent_pass_does_nothing(bench):
    """The lock, held by a pass that is still running.

    #330 is the precedent: mnemo's own helpers fired mnemo's hooks and 42
    unlocked sweeps ran at once. A second pass here must be a no-op, not a
    duplicate of the first.
    """
    import os

    os.mkdir(bench["vault"] / ".mnemo" / rewake.LOCK_NAME)
    sessions = [child("594436f2", bench["trees"][380])]
    report = rewake.sweep(CFG, vault_root=bench["vault"], sessions=sessions,
                          stalls={"594436f2": FREE}, now=NOW,
                          wake_fn=bench["wake"])
    assert report.locked and report.woken == [] and bench["woken"] == []


def test_a_second_watcher_exits_at_once(bench):
    import os

    os.mkdir(bench["vault"] / ".mnemo" / rewake.WATCH_LOCK_NAME)
    report = rewake.watch(CFG, vault_root=bench["vault"],
                          clock=lambda: NOW, sleeper=lambda _s: None,
                          reader=lambda: [], wake_fn=bench["wake"])
    assert report.stopped == "locked" and report.ticks == 0


def test_session_start_starts_one_watcher_and_no_more(bench, monkeypatch):
    """Every session start may try; the lock means one of them wins."""
    spawned = []
    sessions = [child("594436f2", bench["trees"][380])]
    monkeypatch.setattr(rewake, "_read_sessions", lambda: sessions)
    monkeypatch.setattr(rewake.stalls_mod, "stalls_for",
                        lambda found, **k: {"594436f2": WAITING})

    assert rewake.on_session_start(
        CFG, vault_root=bench["vault"], spawn=lambda: spawned.append(1)) == "spawned"
    # The watcher that was spawned now holds its lock.
    import os
    os.mkdir(bench["vault"] / ".mnemo" / rewake.WATCH_LOCK_NAME)
    assert rewake.on_session_start(
        CFG, vault_root=bench["vault"], spawn=lambda: spawned.append(1)) == "running"
    assert len(spawned) == 1


def test_session_start_spawns_nothing_when_there_is_nothing_to_watch(bench, monkeypatch):
    """No daemon on a machine that dispatches nothing."""
    spawned = []
    monkeypatch.setattr(rewake, "_read_sessions", lambda: [])
    assert rewake.on_session_start(
        CFG, vault_root=bench["vault"], spawn=lambda: spawned.append(1)) == "nothing"
    assert spawned == []


def test_session_start_spawns_nothing_when_auto_is_off(bench):
    spawned = []
    assert rewake.on_session_start(
        {"resume": {"auto": False}}, vault_root=bench["vault"],
        spawn=lambda: spawned.append(1)) == "off"
    assert spawned == []


# ---------------------------------------------------------------------------
# the watcher's life
# ---------------------------------------------------------------------------


def _clock(start: float):
    """A clock the sleeper advances, so the loop's own waits are its time."""
    state = {"now": start}

    def now():
        return state["now"]

    def sleep(seconds):
        state["now"] += max(seconds, 1.0)

    return now, sleep, state


def test_the_watcher_exits_when_there_is_nothing_left_to_watch(bench):
    now, sleep, _ = _clock(NOW)
    report = rewake.watch(CFG, vault_root=bench["vault"], clock=now,
                          sleeper=sleep, reader=lambda: [],
                          wake_fn=bench["wake"])
    assert report.stopped == "idle" and report.ticks == 1


def test_the_watcher_waits_out_the_clock_and_then_wakes(bench, monkeypatch):
    """2026-09-19 as a loop: stalled with a reset an hour out, nobody awake.

    The child is abandoned with a future reset from the first tick, so nothing
    is woken until the clock passes it — and then it is, with no command typed.
    """
    now, sleep, state = _clock(NOW)
    stalled = child("594436f2", bench["trees"][380])
    stall = Stall(kind=Kind.RATE_LIMIT, error="rate_limit",
                  resets_at=int(NOW) + 3600, limit="five_hour")

    def _stalls_for(found, **kwargs):
        return {"594436f2": stall} if found else {}

    def _read():
        # Once woken, the child is running again: that is what ends the wait.
        return [] if bench["woken"] else [stalled]

    monkeypatch.setattr(rewake.stalls_mod, "stalls_for", _stalls_for)
    report = rewake.watch(CFG, vault_root=bench["vault"], clock=now,
                          sleeper=sleep, reader=_read, wake_fn=bench["wake"])

    assert len(report.woken) == 1
    assert state["now"] >= stall.resets_at
    assert report.stopped == "idle"


def test_the_watcher_gives_up_after_its_deadline(bench, monkeypatch):
    """The backstop: a reset a week out is not worth a week-long process."""
    now, sleep, state = _clock(NOW)
    stalled = child("594436f2", bench["trees"][380])
    weekly = Stall(kind=Kind.RATE_LIMIT, error="rate_limit",
                   resets_at=int(NOW) + 7 * 86400, limit="seven_day")

    monkeypatch.setattr(rewake.stalls_mod, "stalls_for",
                        lambda found, **k: {"594436f2": weekly})
    report = rewake.watch(CFG, vault_root=bench["vault"], clock=now,
                          sleeper=sleep, reader=lambda: [stalled],
                          wake_fn=bench["wake"], max_seconds=600.0)

    assert report.stopped == "deadline" and report.woken == []
    assert state["now"] - NOW >= 600.0


def test_the_watcher_stays_while_a_child_is_still_running(bench):
    """A running child is a reason to wait: it is what may stall next.

    A session the roster *proves* dead is not, whatever its recorded state —
    otherwise a crashed job would hold a watcher open for six hours with
    nothing to wake.
    """
    working = child("594436f2", bench["trees"][380], live=True,
                    state="running", tempo="working")
    assert rewake.worth_watching([working], {}, {"wakes": {}}, now=NOW) == 1
    finished = child("594436f2", bench["trees"][380], live=True,
                     state="done", tempo="done")
    assert rewake.worth_watching([finished], {}, {"wakes": {}}, now=NOW) == 0
    crashed = child("594436f2", bench["trees"][380], live=False,
                    state="running", tempo="working")
    assert rewake.worth_watching([crashed], {}, {"wakes": {}}, now=NOW) == 0


def test_the_watcher_sleeps_to_the_reset_not_past_it(bench):
    """A reset inside the next tick shortens the sleep to land on it."""
    stalled = child("594436f2", bench["trees"][380])
    soon = Stall(kind=Kind.RATE_LIMIT, error="rate_limit",
                 resets_at=int(NOW) + 7, limit="five_hour")
    nap = rewake._slice([stalled], {"594436f2": soon}, {"wakes": {}},
                        NOW, 60.0, NOW + 3600)
    assert 6.0 <= nap <= 7.0


# ---------------------------------------------------------------------------
# the real stalled child
# ---------------------------------------------------------------------------


FIXTURE = (Path(__file__).resolve().parents[1]
           / "fixtures" / "transcripts" / "stalled_rate_limit_594436f2.jsonl")


def test_wakes_the_real_stalled_child_from_its_transcript(bench, tmp_path):
    """End to end on the transcript PR #394 took from a real stalled child.

    Nothing is hand-built: the stall, its kind and its reset epoch all come out
    of ``594436f2``'s own last record — one of the six children of 2026-09-19 —
    read by the same :func:`stalls.read_stall` the queue uses. The only
    invention is the clock, moved past the reset the child itself reported.
    """
    from mnemo.core.sessions import stalls as stalls_mod

    transcript = tmp_path / "594436f2.jsonl"
    transcript.write_bytes(FIXTURE.read_bytes())
    stalled = Session(
        short_id="594436f2", state="blocked", tempo="blocked", live=False,
        needs="rate limited — wait and retry", cwd=str(bench["trees"][380]),
        session_id="594436f2-a282-4a1c-b04a-08441296047e",
        link_scan_path=str(transcript),
    )
    stall = stalls_mod.read_stall(stalled)
    assert stall.kind == Kind.RATE_LIMIT and stall.resets_at == 1789796400

    # A minute before its own reset: nothing happens.
    early = rewake.sweep(CFG, vault_root=bench["vault"], sessions=[stalled],
                         stalls={"594436f2": stall}, now=stall.resets_at - 60,
                         wake_fn=bench["wake"])
    assert early.woken == [] and early.waiting == 1

    # A minute after it: back at work, with no command typed.
    late = rewake.sweep(CFG, vault_root=bench["vault"], sessions=[stalled],
                        stalls={"594436f2": stall}, now=stall.resets_at + 60,
                        wake_fn=bench["wake"])
    assert late.woken and bench["woken"] == ["594436f2-a282-4a1c-b04a-08441296047e"]


def test_the_credit_cap_fixture_is_never_woken(bench, tmp_path):
    """The other real fixture: a rate limit no clock frees, left for a person."""
    from mnemo.core.sessions import stalls as stalls_mod

    source = FIXTURE.parent / "stalled_credit_cap.jsonl"
    transcript = tmp_path / "credit.jsonl"
    transcript.write_bytes(source.read_bytes())
    stalled = Session(
        short_id="cccccccc", state="blocked", tempo="blocked", live=False,
        cwd=str(bench["trees"][381]),
        session_id="cccccccc-a282-4a1c-b04a-08441296047e",
        link_scan_path=str(transcript),
    )
    stall = stalls_mod.read_stall(stalled)
    assert stall.resets_at is None
    report = rewake.sweep(CFG, vault_root=bench["vault"], sessions=[stalled],
                          stalls={"cccccccc": stall}, now=NOW,
                          wake_fn=bench["wake"])
    assert report.woken == [] and bench["woken"] == []


def test_a_corrupt_ledger_costs_a_duplicate_wake_not_the_pass(bench):
    (bench["vault"] / ".mnemo" / rewake.LEDGER_NAME).write_text("{not json",
                                                                encoding="utf-8")
    sessions = [child("594436f2", bench["trees"][380])]
    report = rewake.sweep(CFG, vault_root=bench["vault"], sessions=sessions,
                          stalls={"594436f2": FREE}, now=NOW,
                          wake_fn=bench["wake"])
    assert len(report.woken) == 1
    assert json.loads((bench["vault"] / ".mnemo" / rewake.LEDGER_NAME)
                      .read_text(encoding="utf-8"))["wakes"]


# ---------------------------------------------------------------------------
# what the maintainer sees afterwards
# ---------------------------------------------------------------------------


def test_the_queue_footer_names_what_mnemo_woke(bench):
    """#396's visibility bar: not only a line in the child's transcript.

    By the time the maintainer looks, a woken child is back under WORKING with
    nothing on its row to say who restarted it. The footer is read off the
    ledger, so it survives the child finishing.
    """
    from mnemo.cli.commands.sessions import _woken_footer

    assert _woken_footer([]) == ""
    rows = [{"short_id": "594436f2", "at_epoch": NOW - 120},
            {"short_id": "aaaaaaaa", "at_epoch": NOW - 180}]
    line = _woken_footer(rows)
    assert "woken by mnemo: 2 child(ren)" in line
    assert "594436f2" in line and "aaaaaaaa" in line


def test_the_queue_footer_collapses_a_whole_dispatch(bench):
    from mnemo.cli.commands.sessions import _woken_footer

    rows = [{"short_id": f"0000000{n}", "at_epoch": NOW - n} for n in range(6)]
    line = _woken_footer(rows)
    assert "6 child(ren)" in line and "+3" in line


def test_the_day_log_records_the_wake(bench, tmp_path, monkeypatch):
    """The other half: a line where session starts and ends already land.

    ``_announce`` is stubbed out everywhere else in this file, so this is the
    one place the real one runs.
    """
    monkeypatch.setattr(rewake, "_announce", _REAL_ANNOUNCE)
    lines: list = []
    monkeypatch.setattr("mnemo.core.log_writer.append_line",
                        lambda agent, content, cfg: lines.append((agent, content)))
    sessions = [child("594436f2", bench["trees"][380])]
    rewake.sweep(CFG, vault_root=bench["vault"], sessions=sessions,
                 stalls={"594436f2": FREE}, now=NOW, wake_fn=bench["wake"])
    assert len(lines) == 1
    assert "mnemo woke 594436f2" in lines[0][1]
    assert "five-hour reset" in lines[0][1]


def test_the_watcher_retires_rather_than_outliving_its_own_version(bench, monkeypatch):
    """A busy machine keeps resetting the idle deadline; the lifetime does not.

    Without this a watcher started today would still be running next week,
    executing code the machine no longer has installed. It hands over instead:
    the next session start begins a fresh one.
    """
    now, sleep, state = _clock(NOW)
    working = child("594436f2", bench["trees"][380], live=True,
                    state="running", tempo="working")
    monkeypatch.setattr(rewake.stalls_mod, "stalls_for", lambda found, **k: {})
    report = rewake.watch(CFG, vault_root=bench["vault"], clock=now,
                          sleeper=sleep, reader=lambda: [working],
                          wake_fn=bench["wake"], max_seconds=60.0,
                          lifetime=300.0)
    assert report.stopped == "retired"
    assert 300.0 <= state["now"] - NOW < 400.0
