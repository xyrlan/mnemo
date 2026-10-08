"""``errors.recent_summary`` / ``errors.remedy_line`` — what the breaker saw (#115)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from mnemo.core import errors


def _log(vault: Path, entries: list[tuple[str, int]], now: datetime) -> None:
    """Ages are minutes before ``now``: one clock reading per test, never one
    per entry, or a minute boundary between readings merges two strikes (#625)."""
    with open(vault / ".errors.log", "a", encoding="utf-8") as fh:
        for where, age_min in entries:
            ts = (now - timedelta(minutes=age_min)).isoformat(timespec="seconds")
            fh.write(json.dumps({"timestamp": ts, "where": where, "kind": "E", "message": "m"}) + "\n")


def test_recent_summary_counts_and_buckets(tmp_path: Path):
    now = datetime.now()
    _log(tmp_path, [("session_start.injection", 5)] * 3 + [("pre_tool_use.x", 10)] * 2
         + [("extract.llm", 1)]                 # excluded, like should_run
         + [("session_end.schedule.spawn", 1)]  # excluded, like should_run
         + [("session_start.injection", 90)], now)  # too old
    count, buckets = errors.recent_summary(tmp_path)
    assert count == 5
    assert buckets == [("session_start.injection", 3), ("pre_tool_use.x", 2)]


def test_recent_summary_no_log(tmp_path: Path):
    assert errors.recent_summary(tmp_path) == (0, [])


def test_recent_summary_skips_garbage_lines(tmp_path: Path):
    _log(tmp_path, [("a", 1)], datetime.now())
    with open(tmp_path / ".errors.log", "a", encoding="utf-8") as fh:
        fh.write("not json\n")
        fh.write(json.dumps({"where": "b"}) + "\n")  # no timestamp
    assert errors.recent_summary(tmp_path) == (1, [("a", 1)])


def test_remedy_line_names_count_top_and_fix(tmp_path: Path):
    _log(tmp_path, [("session_start.injection", 1)] * 11 + [("pre_tool_use.x", 1)], datetime.now())
    line = errors.remedy_line(tmp_path)
    assert line.startswith("circuit breaker open (12 errors in the last hour, most from session_start.injection)")
    assert "`mnemo fix`" in line and "`mnemo doctor`" in line


def test_remedy_line_without_buckets(tmp_path: Path):
    line = errors.remedy_line(tmp_path)
    assert line.startswith("circuit breaker open (0 errors in the last hour).")


def _should_run_agrees_with_recent_summary(vault: Path) -> None:
    now = datetime.now()
    _log(vault, [("x", minutes) for minutes in range(errors.THRESHOLD_PER_HOUR)], now)
    assert errors.should_run(vault) is True
    assert errors.recent_summary(vault)[0] == errors.THRESHOLD_PER_HOUR
    _log(vault, [("x", errors.THRESHOLD_PER_HOUR)], now)
    assert errors.should_run(vault) is False
    assert errors.recent_summary(vault)[0] == errors.THRESHOLD_PER_HOUR + 1


def test_should_run_still_agrees_with_recent_summary(tmp_path: Path):
    _should_run_agrees_with_recent_summary(tmp_path)


class _TickingClock(datetime):
    """A ``datetime`` whose ``now()`` advances half a minute per call (#625):
    any two readings straddle a minute boundary or sit a minute apart."""

    calls = 0
    start = datetime.now()

    @classmethod
    def now(cls, tz=None):
        cls.calls += 1
        return cls.start + timedelta(seconds=30 * (cls.calls - 1))


def test_should_run_agreement_survives_a_minute_straddle(tmp_path: Path, monkeypatch):
    """#625: the clock crossing a minute between writes must not merge two
    entries into one strike. The test's clock starts at second 59 and moves
    30 s per reading, so a helper reading it per entry puts ages 0 and 1 in
    the same minute; ``errors`` keeps the real clock."""
    monkeypatch.setattr(_TickingClock, "start", datetime.now().replace(second=59, microsecond=0))
    monkeypatch.setattr(_TickingClock, "calls", 0)
    monkeypatch.setattr(sys.modules[__name__], "datetime", _TickingClock)
    _should_run_agrees_with_recent_summary(tmp_path)
