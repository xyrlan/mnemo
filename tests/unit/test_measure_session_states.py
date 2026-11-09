"""``tools/measure_session_states.py`` over synthetic job records and transcripts (#737)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_session_states as tool  # noqa: E402

from mnemo.core.sessions import jobs  # noqa: E402

PROMPT = "send me the crash logs"


def _job(root: Path, short_id: str, **fields) -> None:
    d = root / short_id
    d.mkdir(parents=True)
    (d / "state.json").write_text(json.dumps(fields), encoding="utf-8")


def _human(text: str, at: str) -> dict:
    return {"type": "user", "timestamp": at, "origin": {"kind": "human"},
            "message": {"role": "user", "content": text}}


def _transcript(path: Path, *records: dict) -> str:
    path.write_text("".join(json.dumps(r) + "\n" for r in records) + "not json\n",
                    encoding="utf-8")
    return str(path)


def _answered_transcript(path: Path) -> str:
    """A question, the maintainer's answer, then a later turn the write predates."""
    return _transcript(
        path,
        _human("the opening task", "2026-10-19T02:00:00.000Z"),
        {"type": "assistant", "timestamp": "2026-10-19T02:10:00.000Z",
         "message": {"content": [{"type": "text", "text": "Which log do you want?"}]}},
        _human("send me   the\ncrash logs", "2026-10-19T02:33:37.552Z"),
        # The assistant's own loop and Claude Code talking to itself, both
        # after the answer and before the write: neither is a human turn.
        {"type": "user", "timestamp": "2026-10-19T02:33:37.900Z",
         "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
        {"type": "user", "timestamp": "2026-10-19T02:33:38.000Z",
         "origin": {"kind": "task-notification"},
         "message": {"content": "<task-notification>done</task-notification>"}},
        _human("a later prompt the record never saw", "2026-10-19T02:40:00.000Z"),
    )


def test_pairs_are_counted_with_missing_values_kept_apart(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    for i in range(3):
        _job(jobs_dir, f"stop{i}", state="stopped", tempo="idle")
    _job(jobs_dir, "done0", state="done", tempo="idle")
    _job(jobs_dir, "perm0", state="working", tempo="blocked", needs="allow Bash?")
    _job(jobs_dir, "ask00", state="blocked", tempo="blocked", needs="which one?")
    _job(jobs_dir, "bare0")

    report = tool.measure(jobs.read_sessions(jobs_dir, grok=False))

    assert report["records"] == 7
    assert report["pairs"][0] == {"state": "stopped", "tempo": "idle", "count": 3}
    assert {(p["state"], p["tempo"], p["count"]) for p in report["pairs"][1:]} == {
        ("done", "idle", 1), ("working", "blocked", 1), ("blocked", "blocked", 1),
        (None, None, 1),
    }
    # A question and a permission prompt both carry tempo=blocked: neither is
    # the row this tool exists to explain.
    assert report["state_blocked_tempo_not"] == []
    assert tool.MISSING in tool.render(report)


def test_an_answered_question_is_explained_by_its_transcript(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    _job(jobs_dir, "3379a534", state="blocked", tempo="active", detail=PROMPT,
         updatedAt="2026-10-19T02:33:38.172Z",
         linkScanPath=_answered_transcript(tmp_path / "t.jsonl"))

    report = tool.measure(jobs.read_sessions(jobs_dir, grok=False))

    assert report["state_blocked_tempo_not"] == [{
        "short_id": "3379a534", "tempo": "active", "live": None,
        "verdict": "answered", "seconds_after_turn": 0.62,
    }]
    assert report["verdicts"] == {"answered": 1, "no transcript": 0, "unexplained": 0}


def test_a_detail_cut_short_still_matches_its_turn() -> None:
    assert tool.detail_is("send me the cra…", "send me   the\ncrash logs")
    assert tool.detail_is("send me the cra...", "send me the crash logs")
    assert not tool.detail_is("send me the logs", "send me the crash logs")
    assert not tool.detail_is("…", "anything")
    assert not tool.detail_is(None, "anything")
    assert not tool.detail_is("anything", None)


def test_the_last_turn_is_the_one_before_the_write(tmp_path: Path) -> None:
    path = _answered_transcript(tmp_path / "t.jsonl")

    readable, text, at = tool.last_human_turn(path, tool._when("2026-10-19T02:33:38.172Z"))
    assert readable and text == "send me   the\ncrash logs"
    assert at == tool._when("2026-10-19T02:33:37.552Z")

    # Written later, the record would have been answered by the later turn.
    _, later, _ = tool.last_human_turn(path, tool._when("2026-10-19T03:00:00Z"))
    assert later == "a later prompt the record never saw"

    # Written before any human turn: readable, and nothing to match.
    assert tool.last_human_turn(path, tool._when("2026-10-19T01:00:00Z")) == (True, None, None)


def test_rows_the_transcript_cannot_explain_say_so(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    transcript = _answered_transcript(tmp_path / "t.jsonl")
    _job(jobs_dir, "notrans0", state="blocked", tempo="active", detail=PROMPT,
         updatedAt="2026-10-19T02:33:38.172Z")
    _job(jobs_dir, "gone0000", state="blocked", tempo="idle", detail=PROMPT,
         updatedAt="2026-10-19T02:33:38.172Z", linkScanPath=str(tmp_path / "missing.jsonl"))
    _job(jobs_dir, "other000", state="blocked", tempo="active", detail="something else",
         updatedAt="2026-10-19T02:33:38.172Z", linkScanPath=transcript)
    _job(jobs_dir, "notempo0", state="blocked", detail=PROMPT,
         updatedAt="2026-10-19T02:33:38.172Z", linkScanPath=transcript)

    report = tool.measure(jobs.read_sessions(jobs_dir, grok=False))

    verdicts = {r["short_id"]: r["verdict"] for r in report["state_blocked_tempo_not"]}
    assert verdicts == {"notrans0": "no transcript", "gone0000": "no transcript",
                        "other000": "unexplained", "notempo0": "answered"}
    assert report["verdicts"] == {"answered": 1, "no transcript": 2, "unexplained": 1}


def test_the_report_never_quotes_a_detail_or_a_path(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    _job(jobs_dir, "3379a534", state="blocked", tempo="active", detail=PROMPT,
         needs="a question quoting the maintainer",
         updatedAt="2026-10-19T02:33:38.172Z",
         linkScanPath=_answered_transcript(tmp_path / "transcript-of-737.jsonl"))

    report = tool.measure(jobs.read_sessions(jobs_dir, grok=False))
    text = tool.render(report)
    dumped = json.dumps(report)

    # The file name, not the directory: JSON escapes a Windows path's
    # backslashes, so a path compared as a string could never match there.
    for blob in (text, dumped):
        assert "crash" not in blob and "quoting" not in blob
        assert "transcript-of-737" not in blob
    assert "3379a534  tempo=active  live=None  answered, written 0.62s after that turn" in text
    assert "answered 1, no transcript 0, unexplained 0" in text


def test_main_reads_one_jobs_dir_and_stamps_its_provenance(tmp_path: Path, capsys) -> None:
    jobs_dir = tmp_path / "jobs"
    _job(jobs_dir, "done0", state="done", tempo="idle")

    assert tool.main(["--jobs", str(jobs_dir), "--json"]) == 0

    out = json.loads(capsys.readouterr().out)
    assert out["records"] == 1
    assert out["pairs"] == [{"state": "done", "tempo": "idle", "count": 1}]
    assert out["provenance"]["tool"] == "measure_session_states"
    assert out["provenance"]["blind_spots"] == list(tool.BLIND_SPOTS)
