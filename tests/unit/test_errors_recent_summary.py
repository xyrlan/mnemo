"""``errors.recent_summary`` / ``errors.remedy_line`` — what the breaker saw (#115)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from mnemo.core import errors


def _log(vault: Path, entries: list[tuple[str, int]]) -> None:
    with open(vault / ".errors.log", "a", encoding="utf-8") as fh:
        for where, age_min in entries:
            ts = (datetime.now() - timedelta(minutes=age_min)).isoformat(timespec="seconds")
            fh.write(json.dumps({"timestamp": ts, "where": where, "kind": "E", "message": "m"}) + "\n")


def test_recent_summary_counts_and_buckets(tmp_path: Path):
    _log(tmp_path, [("session_start.injection", 5)] * 3 + [("pre_tool_use.x", 10)] * 2
         + [("extract.llm", 1)]                 # excluded, like should_run
         + [("session_end.schedule.spawn", 1)]  # excluded, like should_run
         + [("session_start.injection", 90)])   # too old
    count, buckets = errors.recent_summary(tmp_path)
    assert count == 5
    assert buckets == [("session_start.injection", 3), ("pre_tool_use.x", 2)]


def test_recent_summary_no_log(tmp_path: Path):
    assert errors.recent_summary(tmp_path) == (0, [])


def test_recent_summary_skips_garbage_lines(tmp_path: Path):
    _log(tmp_path, [("a", 1)])
    with open(tmp_path / ".errors.log", "a", encoding="utf-8") as fh:
        fh.write("not json\n")
        fh.write(json.dumps({"where": "b"}) + "\n")  # no timestamp
    assert errors.recent_summary(tmp_path) == (1, [("a", 1)])


def test_remedy_line_names_count_top_and_fix(tmp_path: Path):
    _log(tmp_path, [("session_start.injection", 1)] * 11 + [("pre_tool_use.x", 1)])
    line = errors.remedy_line(tmp_path)
    assert line.startswith("circuit breaker open (12 errors in the last hour, most from session_start.injection)")
    assert "`mnemo fix`" in line and "`mnemo doctor`" in line


def test_remedy_line_without_buckets(tmp_path: Path):
    line = errors.remedy_line(tmp_path)
    assert line.startswith("circuit breaker open (0 errors in the last hour).")


def test_should_run_still_agrees_with_recent_summary(tmp_path: Path):
    _log(tmp_path, [("x", 1)] * errors.THRESHOLD_PER_HOUR)
    assert errors.should_run(tmp_path) is True
    assert errors.recent_summary(tmp_path)[0] == errors.THRESHOLD_PER_HOUR
    _log(tmp_path, [("x", 1)])
    assert errors.should_run(tmp_path) is False
    assert errors.recent_summary(tmp_path)[0] == errors.THRESHOLD_PER_HOUR + 1
