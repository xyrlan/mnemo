"""``tools/measure_briefing_input.py`` over synthetic transcripts and briefings (#766)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_briefing_input as tool  # noqa: E402

from mnemo.core.extract import prompts  # noqa: E402
from mnemo.core.transcript import flatten_transcript_events, user_turns  # noqa: E402


def _user(content, **kw):
    return {"type": "user", "message": {"role": "user", "content": content}, **kw}


def _assistant(*blocks):
    return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}}


CLAUDE_MD = "Contents of CLAUDE.md: " + "rule " * 200


def _session():
    return [
        _user("add a retry helper"),
        {"type": "api-request-blob", "hash": "a", "message": {
            "role": "user", "content": [{"type": "text", "text": CLAUDE_MD}]}},
        {"type": "api-request-blob", "hash": "b", "message": {"role": "system", "content": "You are Claude Code"}},
        {"type": "attachment", "attachment": {"type": "skill_listing", "content": "- a skill"}},
        _assistant({"type": "text", "text": "on it"},
                   {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest"}},
                   {"type": "tool_use", "id": "t2", "name": "Edit", "input": {}}),
        _user([{"type": "tool_result", "tool_use_id": "t1", "content": "x" * 900},
               {"type": "tool_result", "tool_use_id": "t2", "content": "updated"}]),
        _user("<task-notification>build finished</task-notification>"),
        _user("Base directory for this skill: ~/s", isMeta=True),
        _user("This session is being continued. Summary: …", isCompactSummary=True),
        _user("no, only on 5xx"),
    ]


def _prompt(events):
    return prompts.build_briefing_prompt(flatten_transcript_events(events), user_turns=user_turns(events))


def test_after_is_the_prompt_the_briefing_builds_and_before_adds_the_dropped_events():
    events = _session()
    b = tool.breakdown(events)
    assert b["after"] == len(_prompt(events))
    # Before #766 the flattener rendered every event with a message, whatever its type.
    untyped = [e if e["type"] in ("user", "assistant") else {k: v for k, v in e.items() if k != "type"}
               for e in events]
    assert b["before"] == len(_prompt(untyped))
    assert b["before"] - b["after"] == b["parts"]["dropped:api-request-blob"]
    assert "dropped:attachment" not in b["parts"]  # no message: rendered nothing then either


def test_the_parts_of_after_sum_to_after_and_each_lands_in_its_bucket():
    b = tool.breakdown(_session())
    kept = {k: v for k, v in b["parts"].items() if not k.startswith("dropped:")}
    assert sum(kept.values()) == b["after"]
    for bucket in ("tool_result", "tool_use", "assistant text", "user typed",
                   "user synthetic", "user meta", "compact summary"):
        assert kept[bucket] > 0, bucket
    # The 900-character output is cut at 400 the way the briefing cuts it.
    assert 400 < b["by_tool"]["Bash"] < 450
    assert set(b["by_tool"]) == {"Bash", "Edit"}


def test_the_gate_counts_mutations_over_every_event_and_over_the_conversation():
    events = _session() + [{"type": "api-request-blob", "message": {
        "role": "assistant", "content": [{"type": "tool_use", "name": "Write", "input": {}}]}}]
    assert tool.breakdown(events)["gate"] == {"all_events": 2, "conversation": 1}


def test_a_transcript_of_only_bookkeeping_renders_to_the_frame_alone():
    events = [{"type": "api-request-blob", "message": {"role": "user", "content": CLAUDE_MD}}]
    b = tool.breakdown(events)
    assert b["after"] == len(_prompt([])) == b["parts"]["frame"]
    assert b["before"] > b["after"]


def _write(path: Path, events) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def _briefing(vault: Path, agent: str, sid: str, mtime: float) -> None:
    p = vault / "bots" / agent / "briefings" / "sessions" / f"{sid}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\ntype: briefing\n---\n", encoding="utf-8")
    os.utime(p, (mtime, mtime))


def _fixture(tmp_path: Path):
    vault, projects = tmp_path / "vault", tmp_path / "projects"
    _write(projects / "p-one" / "s-new.jsonl", _session())
    _write(projects / "p-two" / "s-old.jsonl", _session())
    _briefing(vault, "proj-a", "s-new", 1_800_000_000)   # 2027-01-15
    _briefing(vault, "proj-a", "s-old", 1_700_000_000)   # 2023-11-14
    _briefing(vault, "proj-b", "s-gone", 1_800_000_100)  # no transcript left
    return vault, projects


def test_sessions_pairs_each_briefing_with_its_transcript_since_a_date(tmp_path: Path):
    vault, projects = _fixture(tmp_path)
    rows, missing = tool.sessions(vault, projects)
    assert [(a, s) for a, s, _ in rows] == [("proj-a", "s-new"), ("proj-a", "s-old")]
    assert rows[0][2] == projects / "p-one" / "s-new.jsonl"
    assert missing == 1
    rows, missing = tool.sessions(vault, projects, since="2026-01-01")
    assert [s for _, s, _ in rows] == ["s-new"] and missing == 1
    rows, _ = tool.sessions(vault, projects, limit=1)
    assert len(rows) == 1


def test_measure_totals_the_sessions(tmp_path: Path):
    vault, projects = _fixture(tmp_path)
    result = tool.measure(tool.sessions(vault, projects)[0])
    one = tool.breakdown(_session())
    assert result["sessions"] == 2
    assert result["before"] == 2 * one["before"] and result["after"] == 2 * one["after"]
    assert result["gate"] == {"passes_all_events": 2, "passes_conversation": 2}


def test_main_prints_the_cut_and_json(tmp_path: Path, capsys):
    vault, projects = _fixture(tmp_path)
    argv = ["--vault", str(vault), "--projects", str(projects)]
    assert tool.main(argv + ["--sessions"]) == 0
    out = capsys.readouterr().out
    assert "sessions=2 no_transcript=1" in out
    assert "dropped:api-request-blob" in out
    assert "s-new" in out
    assert tool.main(argv + ["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["sessions"] == 2 and data["no_transcript"] == 1
