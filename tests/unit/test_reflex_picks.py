"""The judge's pick ledger (#619), over temp vaults whose answers are known by
construction: no real vault, no network."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

from mnemo.core.reflex import picks

DAY = 86400.0


def _t(day: int, hour: int = 12) -> float:
    """Epoch seconds of 2026-09-``day`` at ``hour`` UTC."""
    return datetime(2026, 9, day, hour, tzinfo=timezone.utc).timestamp()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ledger(vault):
    return vault / ".mnemo" / picks.LEDGER_NAME


def _rows(vault):
    path = _ledger(vault)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- the hot path ----------------------------------------------------------------------

def test_record_appends_one_row_with_no_text(tmp_path):
    picks.record(tmp_path, session_id="s1", project="app", picks=["a", "b"], ts=_t(1))
    picks.record(tmp_path, session_id="s1", project="app", picks=[], ts=_t(1, 13))
    rows = _rows(tmp_path)
    assert rows == [
        {"ts": "2026-09-01T12:00:00Z", "session_id": "s1", "project": "app", "picks": ["a", "b"]},
        {"ts": "2026-09-01T13:00:00Z", "session_id": "s1", "project": "app", "picks": []},
    ]


def test_record_stamps_now_when_no_time_is_given(tmp_path):
    picks.record(tmp_path, session_id="s", project="p", picks=["a"])
    assert _rows(tmp_path)[0]["ts"].endswith("Z")


def test_record_never_raises(tmp_path):
    (tmp_path / ".mnemo").write_text("a file where the directory should be", encoding="utf-8")
    picks.record(tmp_path, session_id="s", project="p", picks=["a"])  # no exception


@pytest.mark.skipif(os.name == "nt", reason="a write-only file needs POSIX permissions")
def test_record_never_reads_the_ledger(tmp_path):
    """The hook's cost is one append, whatever the ledger's size: a ledger
    nobody can read still takes the row."""
    picks.record(tmp_path, session_id="s", project="p", picks=["a"], ts=_t(1))
    os.chmod(_ledger(tmp_path), 0o200)
    try:
        picks.record(tmp_path, session_id="s", project="p", picks=["b"], ts=_t(2))
    finally:
        os.chmod(_ledger(tmp_path), 0o600)
    assert [r["picks"] for r in _rows(tmp_path)] == [["a"], ["b"]]


# --- the reader ------------------------------------------------------------------------

def _seed(vault):
    picks.record(vault, session_id="s1", project="app", picks=["a", "b"], ts=_t(1))
    picks.record(vault, session_id="s1", project="app", picks=["a"], ts=_t(1, 14))   # same session: once
    picks.record(vault, session_id="s2", project="app", picks=["a"], ts=_t(3))
    picks.record(vault, session_id="s3", project="other", picks=["a", "c"], ts=_t(4))
    picks.record(vault, session_id="s4", project="app", picks=[], ts=_t(5))


def test_picks_before_counts_distinct_earlier_sessions_per_project(tmp_path):
    _seed(tmp_path)
    assert picks.picks_before(tmp_path, "app", _t(10)) == {"a": 2, "b": 1}
    assert picks.picks_before(tmp_path, "other", _t(10)) == {"a": 1, "c": 1}
    assert picks.picks_before(tmp_path, None, _t(10)) == {"a": 3, "b": 1, "c": 1}


def test_picks_before_is_as_of_t(tmp_path):
    _seed(tmp_path)
    assert picks.picks_before(tmp_path, "app", _t(1)) == {}                 # strictly before
    assert picks.picks_before(tmp_path, "app", _t(2)) == {"a": 1, "b": 1}
    assert picks.picks_before(tmp_path, "app", _t(3, 13)) == {"a": 2, "b": 1}


def test_picks_before_never_counts_the_asking_session(tmp_path):
    _seed(tmp_path)
    assert picks.picks_before(tmp_path, "app", _t(10), session_id="s1") == {"a": 1}
    assert picks.picks_before(tmp_path, "app", _t(10), session_id="s2") == {"a": 1, "b": 1}


def test_a_session_counts_from_its_first_pick(tmp_path):
    picks.record(tmp_path, session_id="s1", project="app", picks=["a"], ts=_t(5))
    picks.record(tmp_path, session_id="s1", project="app", picks=["a"], ts=_t(1))  # out of file order
    assert picks.picks_before(tmp_path, "app", _t(2)) == {"a": 1}


def test_an_empty_or_torn_ledger_reads_as_nothing(tmp_path):
    assert picks.picks_before(tmp_path, "app", _t(10)) == {}
    _ledger(tmp_path).parent.mkdir(parents=True)
    _ledger(tmp_path).write_text('{"ts": "2026-09-01T12:00:00Z", "session_id": "s", "pro\n'
                                 '[1, 2]\n\n', encoding="utf-8")
    assert picks.picks_before(tmp_path, "app", _t(10)) == {}


# --- compaction ------------------------------------------------------------------------

def test_compaction_waits_for_the_cap(tmp_path):
    _seed(tmp_path)
    before = _ledger(tmp_path).read_bytes()
    assert picks.compact(tmp_path, now=_t(30), cap=10_000_000) == "under_cap"
    assert _ledger(tmp_path).read_bytes() == before


def test_compaction_keeps_every_count_as_of_every_day(tmp_path):
    """The point of the summary: rolling rows up changes no answer the reader
    gives at a day boundary, for any project, before or after."""
    for day in range(1, 29):
        for n in range(3):
            sid = "s%d-%d" % (day, n)
            project = "app" if n < 2 else "other"
            picks.record(tmp_path, session_id=sid, project=project,
                         picks=["r%d" % (day % 4), "r%d" % ((day + n) % 5)], ts=_t(day, 8 + n))
            picks.record(tmp_path, session_id=sid, project=project, picks=["r9"], ts=_t(day, 20))
    queries = [(p, _t(d, 0)) for p in ("app", "other", None) for d in range(1, 31)]
    queries.append(("app", _t(30) + 400 * DAY))
    expected = [picks.picks_before(tmp_path, p, t) for p, t in queries]
    size = _ledger(tmp_path).stat().st_size

    assert picks.compact(tmp_path, now=_t(29), cap=1, keep_days=7) == "compacted"

    assert _ledger(tmp_path).stat().st_size < size / 2
    assert (tmp_path / ".mnemo" / picks.SUMMARY_NAME).exists()
    assert [picks.picks_before(tmp_path, p, t) for p, t in queries] == expected
    kept = {r["session_id"] for r in _rows(tmp_path)}
    assert kept and all(int(s.split("-")[0][1:]) >= 22 for s in kept)


def test_compaction_coarsens_old_days_to_months_and_keeps_totals(tmp_path):
    for day in range(1, 29):
        picks.record(tmp_path, session_id="s%d" % day, project="app", picks=["a"], ts=_t(day))
    total = picks.picks_before(tmp_path, "app", _t(30) + 400 * DAY)
    picks.compact(tmp_path, now=_t(28) + 200 * DAY, cap=1, keep_days=7, monthly_after_days=90)
    summary = json.loads((tmp_path / ".mnemo" / picks.SUMMARY_NAME).read_text(encoding="utf-8"))
    assert summary["rules"]["app"]["a"] == {"2026-09": 28}
    assert picks.picks_before(tmp_path, "app", _t(30) + 400 * DAY) == total == {"a": 28}
    # A month bucket counts once the month is over, never in it.
    assert picks.picks_before(tmp_path, "app", _t(29)) == {}


def test_compaction_never_splits_a_session(tmp_path):
    """A session with any row inside the window stays raw whole: rolled up
    halfway, its old half would count in the summary and its new half again."""
    picks.record(tmp_path, session_id="long", project="app", picks=["a"], ts=_t(1))
    picks.record(tmp_path, session_id="long", project="app", picks=["a"], ts=_t(20))
    picks.record(tmp_path, session_id="old", project="app", picks=["a"], ts=_t(2))
    picks.compact(tmp_path, now=_t(21), cap=1, keep_days=7)
    assert {r["session_id"] for r in _rows(tmp_path)} == {"long"}
    assert picks.picks_before(tmp_path, "app", _t(25)) == {"a": 2}
    assert picks.picks_before(tmp_path, "app", _t(25), session_id="long") == {"a": 1}


def test_a_rolled_session_is_never_counted_from_its_own_start(tmp_path):
    """The asking session's own rolled picks fall in a bucket that ends after
    its start, so they are never counted as of that start."""
    picks.record(tmp_path, session_id="s", project="app", picks=["a"], ts=_t(1, 12))
    picks.compact(tmp_path, now=_t(20), cap=1, keep_days=7)
    assert picks.picks_before(tmp_path, "app", _t(1, 11), session_id="s") == {}
    assert picks.picks_before(tmp_path, "app", _t(2, 0)) == {"a": 1}


def test_an_append_racing_the_compaction_is_never_lost(tmp_path, monkeypatch):
    for day in range(1, 10):
        picks.record(tmp_path, session_id="s%d" % day, project="app", picks=["a"], ts=_t(day))
    real = picks._read_rows

    def racing(path):
        rows = list(real(path))
        if path.name.endswith(picks.COMPACTING_SUFFIX):
            picks.record(tmp_path, session_id="late", project="app", picks=["b"], ts=_t(25))
        return rows

    monkeypatch.setattr(picks, "_read_rows", racing)
    picks.compact(tmp_path, now=_t(26), cap=1, keep_days=7)
    monkeypatch.undo()
    assert picks.picks_before(tmp_path, "app", _t(30)) == {"a": 9, "b": 1}


def test_a_compaction_killed_after_the_summary_resumes_without_double_counting(tmp_path, monkeypatch):
    for day in range(1, 10):
        picks.record(tmp_path, session_id="s%d" % day, project="app", picks=["a"], ts=_t(day))
    picks.record(tmp_path, session_id="new", project="app", picks=["b"], ts=_t(25))
    expected = picks.picks_before(tmp_path, "app", _t(30))

    def killed(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(picks, "_append_lines", killed)
    with pytest.raises(KeyboardInterrupt):
        picks.compact(tmp_path, now=_t(26), cap=1, keep_days=7)
    monkeypatch.undo()

    held = _ledger(tmp_path).with_name(picks.LEDGER_NAME + picks.COMPACTING_SUFFIX)
    assert held.exists()
    assert picks.picks_before(tmp_path, "app", _t(30)) == expected     # read mid-compaction
    assert picks.compact(tmp_path, now=_t(26), cap=1, keep_days=7) == "compacted"
    assert not held.exists()
    assert picks.picks_before(tmp_path, "app", _t(30)) == expected


def test_a_second_compaction_adds_to_the_first(tmp_path):
    for day in range(1, 10):
        picks.record(tmp_path, session_id="a%d" % day, project="app", picks=["a"], ts=_t(day))
    picks.compact(tmp_path, now=_t(20), cap=1, keep_days=7)
    for day in range(10, 20):
        picks.record(tmp_path, session_id="b%d" % day, project="app", picks=["a"], ts=_t(day))
    picks.compact(tmp_path, now=_t(30), cap=1, keep_days=7)
    assert _rows(tmp_path) == []
    assert picks.picks_before(tmp_path, "app", _t(30)) == {"a": 19}
    assert picks.picks_before(tmp_path, "app", _t(10, 0)) == {"a": 9}


# --- coverage and backfill -------------------------------------------------------------

def test_since_and_window(tmp_path):
    assert picks.load(tmp_path).since() is None
    _seed(tmp_path)
    ledger = picks.load(tmp_path)
    assert ledger.since() == _t(1)
    assert ledger.window() == (_t(1), _t(4))


def test_backfill_marks_its_rows_and_stops_at_the_first_live_row(tmp_path):
    picks.record(tmp_path, session_id="live", project="app", picks=["a"], ts=_t(10))
    old = [{"ts": _iso(_t(2)), "session_id": "s1", "project": "app", "picks": ["a"]},
           {"ts": _iso(_t(11)), "session_id": "s2", "project": "app", "picks": ["a"]},  # the hook has it
           {"ts": "not a time", "session_id": "s3", "project": "app", "picks": ["a"]}]
    assert picks.backfill(tmp_path, old, covers_since=_t(1)) == 1
    rows = _rows(tmp_path)
    assert [r["session_id"] for r in rows] == ["live", "s1"]
    assert rows[1]["backfilled"] is True and "backfilled" not in rows[0]
    assert picks.load(tmp_path).since() == _t(1)
    assert picks.picks_before(tmp_path, "app", _t(20)) == {"a": 2}


def test_backfill_runs_once(tmp_path):
    rows = [{"ts": _iso(_t(2)), "session_id": "s1", "project": "app", "picks": ["a"]}]
    assert picks.backfill(tmp_path, rows, covers_since=_t(1)) == 1
    with pytest.raises(picks.AlreadyBackfilled):
        picks.backfill(tmp_path, rows, covers_since=_t(1))
    assert len(_rows(tmp_path)) == 1


def test_backfill_survives_compaction(tmp_path):
    rows = [{"ts": _iso(_t(d)), "session_id": "s%d" % d, "project": "app", "picks": ["a"]}
            for d in range(2, 6)]
    picks.backfill(tmp_path, rows, covers_since=_t(1))
    picks.compact(tmp_path, now=_t(30), cap=1, keep_days=7)
    assert picks.load(tmp_path).since() == _t(1)
    with pytest.raises(picks.AlreadyBackfilled):
        picks.backfill(tmp_path, rows, covers_since=_t(1))
    assert picks.picks_before(tmp_path, "app", _t(30)) == {"a": 4}


def test_the_ledger_files_never_hold_prompt_or_rule_text(tmp_path):
    _seed(tmp_path)
    picks.compact(tmp_path, now=_t(30), cap=1, keep_days=7)
    for name in os.listdir(tmp_path / ".mnemo"):
        text = (tmp_path / ".mnemo" / name).read_text(encoding="utf-8") if \
            (tmp_path / ".mnemo" / name).is_file() else ""
        assert "prompt" not in text


def test_a_session_under_two_project_names_counts_once_across_projects(tmp_path):
    picks.record(tmp_path, session_id="s", project="app", picks=["a"], ts=_t(1))
    picks.record(tmp_path, session_id="s", project="app-wt-1", picks=["a"], ts=_t(2))
    assert picks.picks_before(tmp_path, None, _t(3)) == {"a": 1}
    assert picks.picks_before(tmp_path, "app", _t(3)) == {"a": 1}
