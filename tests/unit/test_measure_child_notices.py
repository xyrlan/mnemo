"""``tools/measure_child_notices.py``: which stopped children were never
reported, on synthetic rosters, report logs, daemon lines and transcripts
shaped like the real ones (#502)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_child_notices as tool  # noqa: E402

PARENT = "0ff9d810-e54d-41f3-a045-b0ccff6c5186"


def _session(short: str, *, state: str = "stopped", at: str = "2026-09-24T20:00:05.000Z") -> dict:
    return {"short_id": short, "session_id": f"{short}-full", "state": state,
            "updated_at": at, "cwd": f"/x/app-wt-{short}"}


def _stop_transcript(stop: str, turn: str = "") -> list:
    """A closing report, then ``claude stop`` — the shape every child ends on."""
    return [
        json.dumps({"type": "assistant", "timestamp": turn or stop,
                    "message": {"content": [{"type": "text", "text": "report"}]}}),
        json.dumps({"type": "assistant", "timestamp": stop, "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "claude stop ${CLAUDE_CODE_SESSION_ID:0:8}"}}]}}),
        json.dumps({"type": "cost-state"}),
    ]


def test_readers_parse_the_real_line_shapes():
    assert tool.parse_parents([
        json.dumps({"short_id": "aaaaaaaa", "parent_session": PARENT}), "not json", "",
    ]) == {"aaaaaaaa": PARENT}
    settled = tool.parse_settled([
        "[2026-09-24T20:00:08.500Z] [bg] bg settled aaaaaaaa (killed)",
        "[2026-09-24T20:00:09.000Z] [bg] something else",
    ])
    assert list(settled) == ["aaaaaaaa"]
    t = tool.read_transcript(_stop_transcript("2026-09-24T20:00:05.000Z", "2026-09-24T20:00:01.000Z"))
    assert t["stop"] == t["turn"]  # the stop call is itself the last turn
    assert t["stop"] is not None


def test_report_row_stamps_with_an_offset_parse():
    utc = tool._epoch("2026-09-24T20:00:07Z")
    assert tool._epoch("2026-09-24T17:00:07-0300") == utc


def _measure(roster, *, reports=(), cache=(), since="2026-09-24T12"):
    transcripts = {f"{s['short_id']}-full": tool.read_transcript(_stop_transcript("2026-09-24T20:00:02.000Z"))
                   for s in roster}
    return tool.measure(
        roster, since=since,
        parents={s["short_id"]: PARENT for s in roster if s["short_id"] != "nolinkxx"},
        reports=tool.parse_reports(json.dumps(r) for r in reports),
        settled=tool.parse_settled([
            f"[2026-09-24T20:00:0{5 + i}.000Z] [bg] bg settled {s['short_id']} (killed)"
            for i, s in enumerate(roster)
        ]),
        cache_left=lambda full: full[:8] in cache,
        transcript=lambda full: transcripts.get(full),
    )


def test_groups_follow_the_issue_and_split_the_backstop_out():
    roster = [_session("hookhook"), _session("backstop"), _session("neverran"),
              _session("ranquiet"), _session("nolinkxx"), _session("workingx", state="working"),
              _session("tooearly", at="2026-09-23T00:00:00.000Z")]
    rows = _measure(roster, reports=[
        {"ts": "2026-09-24T17:00:03-0300", "short_id": "hookhook", "event": "spawned"},
        {"ts": "2026-09-24T17:02:10-0300", "short_id": "backstop", "event": "spawned", "via": "backstop"},
    ], cache={"neverran"})
    got = {r["short_id"]: r["group"] for r in rows}
    assert got == {
        "hookhook": tool.REPORTED_HOOK,
        "backstop": tool.REPORTED_BACKSTOP,
        "neverran": tool.SILENT_NEVER_RAN,
        "ranquiet": tool.SILENT_RAN,
    }
    by = {r["short_id"]: r for r in rows}
    assert by["hookhook"]["turn_to_report"] == 1.0
    assert by["backstop"]["turn_to_report"] == 128.0
    assert by["neverran"]["turn_to_report"] is None
    assert all(r["self_stopped"] for r in rows)
    assert by["hookhook"]["stop_to_settled"] == 3.0


def test_summary_and_render():
    roster = [_session("hookhook"), _session("neverran"), _session("neverra2")]
    rows = _measure(roster, reports=[
        {"ts": "2026-09-24T17:00:03-0300", "short_id": "hookhook", "event": "spawned"},
    ], cache={"neverran", "neverra2"})
    summary = tool.summarize(rows)
    assert summary["total"] == 3
    assert summary["groups"][tool.SILENT_NEVER_RAN]["children"] == 2
    assert summary["groups"][tool.SILENT_NEVER_RAN]["stop_to_settled_range"] == [4.0, 5.0]
    assert summary["groups"][tool.REPORTED_HOOK]["turn_to_report_max"] == 1.0
    text = tool.render(summary, "2026-09-24T12")
    assert "dispatched children stopped since 2026-09-24T12: 3" in text
    assert "parents never told: 2 of 3" in text


def test_simulate_runs_the_shipped_scan(tmp_path):
    """One grace period after each stop, the backstop's own scan tells the
    silent child and leaves the reported one alone."""
    from mnemo.core.sessions import child_notices

    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    (vault / ".mnemo" / "child-reports.jsonl").write_text(json.dumps({
        "ts": "2026-09-24T17:00:03-0300", "short_id": "hookhook", "event": "spawned",
    }) + "\n", encoding="utf-8")
    roster = [_session("hookhook"), _session("neverran")]
    rows = _measure(roster, reports=[
        {"ts": "2026-09-24T17:00:03-0300", "short_id": "hookhook", "event": "spawned"},
    ], cache={"neverran"})
    turn = tool._epoch("2026-09-24T20:00:02.000Z")
    got = tool.simulate(
        roster, rows, links={"hookhook": PARENT, "neverran": PARENT},
        reports=child_notices.report_rows(vault), find=lambda _s: None,
        turn_of=lambda _t: turn,
    )
    assert got[tool.REPORTED_HOOK] == {"due": 0, "told": 1}
    assert got[tool.SILENT_NEVER_RAN] == {"due": 1, "told": 0}
