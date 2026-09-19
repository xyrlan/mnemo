"""``tools/measure_wake_latency.py`` over synthetic transcripts and logs (#396).

The tool's numbers are the argument for the shape of the trigger, so the
arithmetic behind them is pinned here rather than trusted. Everything is
built: a projects tree with one transcript per case, and a vault whose day
logs hold the hook events at chosen minutes.

The timezone matters and is the one thing that went wrong while writing it. A
transcript stamp is UTC; a day-log line is local wall-clock. Reading the first
as local moved every stall three hours and put one of them *after* its own
reset, which would have reported a trigger as being in time when it was not.
:func:`test_a_utc_stamp_is_not_read_as_local` is that bug as a test.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

_TOOL = (Path(__file__).resolve().parents[2] / "tools" / "measure_wake_latency.py")
_spec = importlib.util.spec_from_file_location("measure_wake_latency", _TOOL)
mwl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mwl)


def _api_error(resets_at, *, stamp: str, error: str = "rate_limit",
               limit: str = "five_hour") -> dict:
    quota = None if resets_at is None else {
        "status": "rejected", "resetsAt": resets_at, "rateLimitType": limit,
    }
    return {
        "type": "assistant", "isApiErrorMessage": True, "error": error,
        "timestamp": stamp, "quotaLimits": quota,
        "message": {"content": [{"type": "text", "text": "You've hit your session limit"}]},
    }


@pytest.fixture()
def bench(tmp_path):
    projects = tmp_path / "projects"
    (projects / "-Users-x-repo").mkdir(parents=True)
    vault = tmp_path / "vault"
    return {"projects": projects, "vault": vault,
            "dir": projects / "-Users-x-repo"}


def _write_transcript(bench, name: str, records) -> None:
    (bench["dir"] / f"{name}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _write_log(bench, day: str, minutes) -> None:
    logs = bench["vault"] / "bots" / "repo" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"- **{m}** — 🟢 session started (startup)" for m in minutes)
    (logs / f"{day}.md").write_text(f"# {day}\n\n{body}\n", encoding="utf-8")


def _local_epoch(day: str, hhmm: str) -> int:
    year, month, dom = (int(x) for x in day.split("-"))
    hour, minute = (int(x) for x in hhmm.split(":"))
    return int(datetime(year, month, dom, hour, minute).astimezone().timestamp())


def _utc_stamp(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------


def test_only_a_rate_limit_with_an_epoch_is_counted(bench):
    reset = _local_epoch("2026-09-19", "02:40")
    _write_transcript(bench, "with-epoch",
                      [_api_error(reset, stamp=_utc_stamp(reset - 6000))])
    _write_transcript(bench, "credit-cap",
                      [_api_error(None, stamp=_utc_stamp(reset - 6000))])
    _write_transcript(bench, "login",
                      [_api_error(None, stamp=_utc_stamp(reset - 6000),
                                  error="authentication_failed")])
    _write_log(bench, "2026-09-19", ["00:43", "06:29"])

    report = mwl.measure(str(bench["projects"]), str(bench["vault"]))
    assert report["transcripts"] == 3
    assert report["rate_limits_with_epoch"] == 1
    assert len(report["resets"]) == 1


def test_latency_is_the_first_hook_at_or_after_the_reset(bench):
    """The 2026-09-19 shape: stalled 00:51, free 02:40, first hook 06:29."""
    reset = _local_epoch("2026-09-19", "02:40")
    stall = _local_epoch("2026-09-19", "00:51")
    _write_transcript(bench, "594436f2", [_api_error(reset, stamp=_utc_stamp(stall))])
    _write_log(bench, "2026-09-19", ["00:43", "06:29", "07:15"])

    row = mwl.measure(str(bench["projects"]), str(bench["vault"]))["resets"][0]
    assert row["latency_minutes"] == 229
    # Nothing ran between the stall and the reset…
    assert row["armed"] is False
    # …but something ran eight minutes before the stall, which is when a
    # watcher would have been started.
    assert row["hook_before_minutes"] == 8


def test_a_hook_inside_the_window_counts_as_armed(bench):
    reset = _local_epoch("2026-09-19", "02:40")
    stall = _local_epoch("2026-09-19", "00:51")
    _write_transcript(bench, "594436f2", [_api_error(reset, stamp=_utc_stamp(stall))])
    _write_log(bench, "2026-09-19", ["00:43", "01:30", "02:45"])

    row = mwl.measure(str(bench["projects"]), str(bench["vault"]))["resets"][0]
    assert row["armed"] is True
    assert row["latency_minutes"] == 5


def test_a_reset_still_in_the_future_has_no_latency(bench):
    reset = _local_epoch("2026-09-19", "02:40")
    stall = _local_epoch("2026-09-19", "00:51")
    _write_transcript(bench, "594436f2", [_api_error(reset, stamp=_utc_stamp(stall))])
    _write_log(bench, "2026-09-19", ["00:43"])

    row = mwl.measure(str(bench["projects"]), str(bench["vault"]))["resets"][0]
    assert row["latency_minutes"] is None
    assert "—" in mwl.render(mwl.measure(str(bench["projects"]), str(bench["vault"])))


def test_children_on_one_reset_are_one_row(bench):
    """Six children of one dispatch share a window; the report says six, once."""
    reset = _local_epoch("2026-09-19", "02:40")
    stall = _local_epoch("2026-09-19", "00:51")
    for n in range(6):
        _write_transcript(bench, f"child{n}",
                          [_api_error(reset, stamp=_utc_stamp(stall + n))])
    _write_log(bench, "2026-09-19", ["00:43", "06:29"])

    report = mwl.measure(str(bench["projects"]), str(bench["vault"]))
    assert len(report["resets"]) == 1
    assert report["resets"][0]["children"] == 6
    assert report["rate_limits_with_epoch"] == 6


def test_a_utc_stamp_is_not_read_as_local(bench):
    """The bug this tool shipped with for one run, pinned.

    ``2026-09-19T03:51:57Z`` is 00:51 in ``America/Sao_Paulo``, where the
    incident happened. Parsed naive and read back as local it became 06:51 —
    *after* its own 02:40 reset, which makes a stall look like it happened
    inside a window it was in fact waiting for.

    Asserted against the epoch rather than against a wall-clock string, so the
    test says the same thing on a CI box running UTC.
    """
    stamp = "2026-09-19T03:51:57Z"
    assert mwl._epoch(stamp) == datetime(
        2026, 9, 19, 3, 51, 57, tzinfo=timezone.utc).timestamp()
    # A stall an hour before its reset, whatever this machine calls the hour.
    reset = _local_epoch("2026-09-19", "02:40")
    stalled = reset - 3600
    _write_transcript(bench, "594436f2",
                      [_api_error(reset, stamp=_utc_stamp(stalled))])
    _write_log(bench, "2026-09-19", ["06:29"])
    row = mwl.measure(str(bench["projects"]), str(bench["vault"]))["resets"][0]
    assert row["stalled_at_local"] == datetime.fromtimestamp(stalled).strftime(
        "%Y-%m-%d %H:%M")
    assert row["armed"] is False


def test_hook_events_come_from_the_day_logs_of_every_project(bench):
    _write_log(bench, "2026-09-19", ["00:43", "06:29"])
    other = bench["vault"] / "bots" / "clubinho" / "logs"
    other.mkdir(parents=True)
    (other / "2026-09-19.md").write_text(
        "- **03:00** — 🔴 session ended (other)\n", encoding="utf-8")
    events = mwl.read_hook_events(str(bench["vault"]))
    assert len(events) == 3
    assert events == sorted(events)


def test_an_unreadable_tree_is_empty_not_fatal(tmp_path):
    report = mwl.measure(str(tmp_path / "nope"), str(tmp_path / "also-nope"))
    assert report["transcripts"] == 0 and report["resets"] == []
    assert mwl.render(report)


def test_the_window_helpers_agree_with_bisect(bench):
    events = [10.0, 20.0, 30.0]
    assert mwl.first_event_after(events, 20.0) == 20.0
    assert mwl.first_event_after(events, 21.0) == 30.0
    assert mwl.first_event_after(events, 31.0) is None
    assert mwl.last_event_before(events, 20.0) == 10.0
    assert mwl.last_event_before(events, 10.0) is None
    assert mwl.any_event_between(events, 15.0, 25.0) is True
    assert mwl.any_event_between(events, 21.0, 29.0) is False
