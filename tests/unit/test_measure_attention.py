"""``tools/measure_attention.py``: how long dispatched children wait on their
human, before and after the Nexus (#662), over synthetic job timelines,
transcripts and desktop usage rows."""
from __future__ import annotations

import json
import sys
import time
from datetime import date, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_attention as tool  # noqa: E402

T0 = 1_790_000_000.0  # 2026-09-21 14:13:20 UTC
MIN = 60.0
DAY = 86400.0


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


def _row(at: float, state: str, text: str = "", detail: str = "") -> Dict[str, Any]:
    return {"at": _iso(at), "state": state, "detail": detail or text[:40], "text": text}


def _api_error(at: float, error: str, resets_at: Optional[int] = None) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "type": "assistant", "isApiErrorMessage": True, "error": error,
        "timestamp": _iso(at),
        "message": {"model": "<synthetic>", "content": [{"type": "text", "text": "API Error"}]},
    }
    if resets_at is not None:
        record["quotaLimits"] = {"resetsAt": resets_at, "rateLimitType": "five_hour"}
    return record


def _job(jobs: Path, name: str, rows: Sequence[Dict[str, Any]],
         transcript: Optional[Sequence[Dict[str, Any]]] = ()) -> None:
    """A job dir. ``transcript=None`` points state.json at a file that is gone."""
    d = jobs / name
    d.mkdir(parents=True)
    (d / "timeline.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    path = d / "transcript.jsonl"
    if transcript is not None:
        path.write_text("".join(json.dumps(r) + "\n" for r in transcript), encoding="utf-8")
    (d / "state.json").write_text(json.dumps({"linkScanPath": str(path)}), encoding="utf-8")


def _eps(rows: Sequence[Dict[str, Any]], kind: str = tool.ASK) -> List[Dict[str, Any]]:
    parsed = []
    for i, r in enumerate(rows):
        parsed.append({"at": tool.epoch(r["at"]), "i": i, "state": r["state"], "text": r["text"]})
    return tool.episodes(parsed, lambda at: kind, job="j")


# --- episodes --------------------------------------------------------------

def test_episode_runs_from_blocked_to_the_next_state() -> None:
    eps = _eps([_row(T0, "working"), _row(T0 + MIN, "blocked", "May I?"),
                _row(T0 + 11 * MIN, "working")])
    assert len(eps) == 1
    assert eps[0]["minutes"] == pytest.approx(10)
    assert eps[0]["ended_by"] == "working"


def test_a_reply_while_blocked_closes_the_episode_at_the_reply() -> None:
    """The human's prompt lands as a blocked row with no text, before the state moves."""
    eps = _eps([_row(T0, "blocked", "May I create ok.txt?"),
                _row(T0 + 4 * MIN, "blocked", "", detail="yes, go"),
                _row(T0 + 30 * MIN, "working")])
    assert [e["minutes"] for e in eps] == [pytest.approx(4)]
    assert eps[0]["ended_by"] == "reply"


def test_a_re_reported_wait_is_one_episode() -> None:
    eps = _eps([_row(T0 + i, "blocked", "limit") for i in range(18)]
               + [_row(T0 + 60 * MIN, "working")])
    assert len(eps) == 1
    assert eps[0]["minutes"] == pytest.approx(60)


def test_a_new_kind_while_blocked_starts_a_new_episode() -> None:
    rows = [_row(T0, "blocked", "server error"), _row(T0 + 5 * MIN, "blocked", "credits")]
    kinds = {tool.epoch(rows[0]["at"]): tool.API_TRANSIENT,
             tool.epoch(rows[1]["at"]): tool.API_PERSON}
    parsed = [{"at": tool.epoch(r["at"]), "i": i, "state": r["state"], "text": r["text"]}
              for i, r in enumerate(rows)]
    eps = tool.episodes(parsed, kinds.__getitem__)
    assert [(e["kind"], e["ended_by"]) for e in eps] == [
        (tool.API_TRANSIENT, "blocked"), (tool.API_PERSON, None)]
    assert eps[0]["minutes"] == pytest.approx(5)


def test_an_episode_the_record_never_closes_is_open_not_dropped() -> None:
    eps = _eps([_row(T0, "working"), _row(T0 + MIN, "blocked", "awaiting verdict")])
    assert len(eps) == 1
    assert eps[0]["end"] is None and eps[0]["minutes"] is None


def test_a_reply_after_the_episode_closed_opens_nothing() -> None:
    eps = _eps([_row(T0, "blocked", "q?"), _row(T0 + MIN, "blocked", ""),
                _row(T0 + 2 * MIN, "blocked", ""), _row(T0 + 3 * MIN, "stopped")])
    assert len(eps) == 1


# --- kinds, from the transcript as stalls.py reads it ----------------------

def test_a_job_with_no_transcript_is_unread() -> None:
    errors = tool.api_errors(None)
    assert tool.kind_at(T0, errors) == tool.UNREAD


@pytest.mark.parametrize("record,kind", [
    (_api_error(T0, "rate_limit", resets_at=int(T0 + 3600)), tool.RATE_LIMIT),
    (_api_error(T0, "rate_limit"), tool.API_PERSON),  # a spent credit: no epoch
    (_api_error(T0, "server_error"), tool.API_TRANSIENT),
    (_api_error(T0, "authentication_failed"), tool.API_PERSON),
])
def test_each_stall_kind_maps(tmp_path: Path, record: Dict[str, Any], kind: str) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    assert tool.kind_at(T0 + 0.2, tool.api_errors(str(path))) == kind


def test_an_api_error_far_from_the_row_is_not_its_error(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps(_api_error(T0, "rate_limit", resets_at=int(T0))) + "\n",
                    encoding="utf-8")
    assert tool.kind_at(T0 + tool.MATCH_SECONDS + 1, tool.api_errors(str(path))) == tool.ASK


def test_read_jobs_sets_rate_limits_apart(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    _job(jobs, "aaaa0001", [_row(T0, "blocked", "You've hit your session limit"),
                            _row(T0 + 300 * MIN, "working")],
         [_api_error(T0, "rate_limit", resets_at=int(T0 + 3600))])
    _job(jobs, "aaaa0002", [_row(T0, "blocked", "May I?"), _row(T0 + 9 * MIN, "blocked", "")])
    _job(jobs, "aaaa0003", [_row(T0, "blocked", "Connection lost")], None)
    _job(jobs, "aaaa0004", [_row(T0, "working"), _row(T0 + MIN, "done")])
    eps, seen = tool.read_jobs([jobs, tmp_path / "missing"])
    assert seen == {"dirs": 1, "jobs": 4, "jobs_blocked": 3}
    assert sorted((e["job"], e["kind"]) for e in eps) == [
        ("aaaa0001", tool.RATE_LIMIT), ("aaaa0002", tool.ASK), ("aaaa0003", tool.UNREAD)]
    report = tool.summarize(eps, start=tool.after_start(flag=None, stamp=None, stamp_nexus=None,
                                                        first_event=None),
                            nexus_days=set(), seen=seen, boot=50, tz=timezone.utc)
    before = report["before"]
    assert before["episodes"]["n"] == 1
    assert before["episodes"]["median"] == pytest.approx(9)
    assert before["by_kind"][tool.RATE_LIMIT] == {"ended": 1, "open": 0}
    assert before["by_kind"][tool.UNREAD] == {"ended": 0, "open": 1}
    assert before["open"] == 0


# --- after: the install, and attribution -----------------------------------

def test_the_flag_wins() -> None:
    start = tool.after_start(flag=T0, stamp={"installed_at": _iso(T0 - DAY), "sha": "a"},
                             stamp_nexus=True, first_event=T0 - 2 * DAY)
    assert (start["at"], start["source"]) == (T0, "flag")


def test_the_earliest_proof_of_a_nexus_install_starts_after() -> None:
    stamp = {"installed_at": _iso(T0 + DAY), "sha": "abc"}
    start = tool.after_start(flag=None, stamp=stamp, stamp_nexus=True, first_event=T0 + 2 * DAY)
    assert (start["at"], start["source"]) == (T0 + DAY, "stamp")
    # A reinstall moved the stamp past the first open: the open is the tighter bound.
    start = tool.after_start(flag=None, stamp=stamp, stamp_nexus=True, first_event=T0)
    assert (start["at"], start["source"]) == (T0, "first_nexus_event")


def test_a_stamp_not_known_to_hold_the_nexus_is_not_used() -> None:
    stamp = {"installed_at": _iso(T0), "sha": "abc"}
    for verdict in (None, False):
        start = tool.after_start(flag=None, stamp=stamp, stamp_nexus=verdict, first_event=None)
        assert start["at"] is None
        assert start["evidence"]["stamp_installed_at"] == T0


def _fake_git(answers: Dict[str, Optional[str]]):
    def run(args: Sequence[str]) -> Optional[str]:
        return answers.get(args[0])
    return run


def test_stamp_has_nexus_asks_git_for_ancestry() -> None:
    stamp = {"sha": "abc1234"}
    yes = _fake_git({"rev-parse": "x", "log": "n1\nn2", "merge-base": ""})
    no = _fake_git({"rev-parse": "x", "log": "n1", "merge-base": None})
    none_yet = _fake_git({"rev-parse": "x", "log": ""})
    assert tool.stamp_has_nexus(stamp, yes) is True
    assert tool.stamp_has_nexus(stamp, no) is False
    assert tool.stamp_has_nexus(stamp, none_yet) is None
    assert tool.stamp_has_nexus(stamp, None) is None
    assert tool.stamp_has_nexus({}, yes) is None


def test_nexus_days_are_days_it_was_opened_or_on_screen() -> None:
    usage = [
        {"event": "beat", "ts": T0 - DAY},
        {"event": "nexus.visible", "ms": 0, "ts": T0},           # registered, never seen
        {"event": "nexus.open", "ts": T0 + DAY},
        {"event": "nexus.visible", "ms": 5000, "ts": T0 + 3 * DAY},
    ]
    first, days = tool.nexus_use(usage, timezone.utc)
    assert first == T0
    assert days == {date(2026, 9, 22), date(2026, 9, 24)}


def test_after_is_split_by_nexus_days_and_differenced() -> None:
    def ep(start: float, minutes: float) -> Dict[str, Any]:
        return {"job": "j", "kind": tool.ASK, "start": start, "end": start + minutes * MIN,
                "minutes": minutes, "ended_by": "reply"}
    before = [ep(T0 - 10 * DAY + i * MIN, m) for i, m in enumerate([40, 50, 60])]
    on_day = [ep(T0 + DAY, 5), ep(T0 + DAY + MIN, 7)]
    off_day = [ep(T0 + 2 * DAY, 45)]
    start = tool.after_start(flag=T0, stamp=None, stamp_nexus=None, first_event=None)
    report = tool.summarize(before + on_day + off_day, start=start,
                            nexus_days={date(2026, 9, 22)}, seen={"dirs": 1, "jobs": 1,
                                                                  "jobs_blocked": 1},
                            boot=200, tz=timezone.utc)
    assert report["before"]["episodes"]["median"] == pytest.approx(50)
    assert report["after"]["episodes"]["n"] == 3
    assert report["after_nexus_days"]["episodes"]["median"] == pytest.approx(6)
    assert report["after_other_days"]["episodes"]["median"] == pytest.approx(45)
    assert report["difference"]["median"] == pytest.approx(7 - 50)
    assert report["difference_nexus_days"]["median"] == pytest.approx(6 - 50)
    lo, hi = report["difference"]["median_ci"]
    assert lo <= report["difference"]["median"] <= hi
    text = tool.format_report(report)
    assert "after starts 2026-09-21 14:13 UTC, from flag" in text
    assert "after, days the Nexus was open: 2 ended episodes" in text
    assert "(thin: under 10)" in text


# --- statistics ------------------------------------------------------------

def test_describe_is_seeded_and_brackets_the_estimate() -> None:
    values = [float(v) for v in range(1, 40)]
    a = tool.describe(values, boot=300, seed=7)
    assert a == tool.describe(values, boot=300, seed=7)
    assert a["median"] == pytest.approx(20)
    assert a["median_ci"][0] <= 20 <= a["median_ci"][1]
    assert a["p90_ci"][0] <= a["p90"] <= a["p90_ci"][1]


def test_an_empty_side_has_no_statistics() -> None:
    assert tool.describe([], boot=10, seed=0) == {
        "n": 0, "median": None, "median_ci": [None, None], "p90": None, "p90_ci": [None, None]}
    assert tool.difference([], [1.0], boot=10, seed=0) is None


def test_epoch_reads_iso_unix_and_rejects_the_rest() -> None:
    assert tool.epoch("2026-09-21T14:13:20.000Z") == T0
    assert tool.epoch(str(int(T0))) == T0
    assert tool.epoch(T0) == T0
    assert tool.epoch(True) is None
    assert tool.epoch("soon") is None


# --- end to end ------------------------------------------------------------

def test_main_text_and_json(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    jobs = tmp_path / "jobs"
    _job(jobs, "bbbb0001", [_row(T0, "blocked", "May I?"), _row(T0 + 20 * MIN, "working")])
    _job(jobs, "bbbb0002", [_row(T0 + 2 * DAY, "blocked", "verdict?"),
                            _row(T0 + 2 * DAY + 3 * MIN, "blocked", "")])
    usage = tmp_path / "usage.jsonl"
    usage.write_text(json.dumps({"event": "nexus.open", "ts": T0 + 2 * DAY}) + "\n",
                     encoding="utf-8")
    common = ["--jobs", str(jobs), "--usage", str(usage), "--stamp", str(tmp_path / "none.json"),
              "--desktop-repo", str(tmp_path / "no-repo"), "--boot", "50"]
    assert tool.main(common) == 0
    text = capsys.readouterr().out
    assert "sample: 2 jobs in 1 config dir(s), 2 with a blocked row" in text
    assert "from first_nexus_event" in text
    assert "before: 1 ended episodes" in text
    assert "provenance: " in text

    assert tool.main(common + ["--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["after_start"]["source"] == "first_nexus_event"
    assert report["before"]["episodes"]["n"] == 1
    assert report["after"]["episodes"]["n"] == 1
    assert report["after"]["episodes"]["median"] == pytest.approx(3)
    assert "provenance" in report


def test_main_refuses_an_unreadable_after(tmp_path: Path) -> None:
    assert tool.main(["--after", "someday", "--jobs", str(tmp_path)]) == 2
