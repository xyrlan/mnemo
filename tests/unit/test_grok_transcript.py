"""A Grok history reads as the events mnemo reads from a Claude Code transcript."""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core import briefing
from mnemo.core.grok import transcript as gt
from mnemo.core.transcript import flatten_transcript_events, user_turn_records, user_turns
from tests.unit._grok_history import CWD, PROMPT, SESSION_ID, write_session


def _user_record(text: str, reason: str = "", prompt_index=None) -> dict:
    record = {"type": "user", "content": [{"type": "text", "text": text}]}
    if reason:
        record["synthetic_reason"] = reason
    if prompt_index is not None:
        record["prompt_index"] = prompt_index
    return record


def test_the_users_words_are_the_prompt_alone(tmp_path: Path):
    history = write_session(tmp_path / ".grok")

    assert user_turns(gt.load_events(history)) == [PROMPT]


def test_injected_records_are_not_user_turns_and_stay_findable(tmp_path: Path):
    records = [
        _user_record("<user_info>\nWorkspace Path: /Users/you/proj\n</user_info>"),
        _user_record("<user_query>\nfix the parser\n</user_query>", prompt_index=0),
        _user_record("# AGENTS.md\nalways use tabs", reason="project_instructions"),
        _user_record("Background task finished: exit 0", reason="task_completed", prompt_index=1),
        _user_record("<user_query>\nhook says no\n</user_query>", reason="stop_hook_feedback"),
        _user_record("<user_query>\nno, stop — use spaces\n</user_query>", reason="interjection"),
        _user_record("<system-reminder>\nmnemo canary\n</system-reminder>", reason="system_reminder"),
    ]
    history = write_session(tmp_path / ".grok", records=records)

    events = gt.load_events(history)

    assert user_turns(events) == ["fix the parser", "no, stop — use spaces"]
    injected = [e for e in events if e.get("subtype") == gt.INJECTED_SUBTYPE]
    assert [e["synthetic_reason"] for e in injected] == [
        "session_prefix", "project_instructions", "task_completed", "stop_hook_feedback",
        "system_reminder",
    ]
    assert "mnemo canary" in injected[-1]["content"]
    flat = flatten_transcript_events(events)
    assert "AGENTS.md" not in flat and "canary" not in flat and "user_info" not in flat


def test_tool_calls_keep_grok_names_and_parsed_input(tmp_path: Path):
    events = gt.load_events(write_session(tmp_path / ".grok"))

    uses = [
        b for e in events if e["type"] == "assistant"
        for b in e["message"]["content"] if b["type"] == "tool_use"
    ]
    assert [b["name"] for b in uses] == ["write", "search_replace", "run_terminal_command", "read_file"]
    assert uses[1]["input"] == {
        "file_path": f"{CWD}/notes.txt", "old_string": "alpha", "new_string": "beta",
    }
    assert uses[3]["input"] == {"target_file": f"{CWD}/notes.txt"}
    results = [
        b for e in events if e["type"] == "user" and isinstance(e["message"]["content"], list)
        for b in e["message"]["content"]
    ]
    assert [(b["type"], b["tool_use_id"]) for b in results][:2] == [
        ("tool_result", "call-3b51-0"), ("tool_result", "call-9df6-1"),
    ]


def test_the_briefing_counts_grok_edits_as_mutations(tmp_path: Path):
    events = gt.load_events(write_session(tmp_path / ".grok"))

    # write + search_replace; the shell command and the read are not edits.
    assert briefing._count_file_mutations(events) == 2


def test_flattened_transcript_reads_like_a_claude_one(tmp_path: Path):
    flat = flatten_transcript_events(gt.load_events(write_session(tmp_path / ".grok")))

    assert flat.startswith(f"[user] {PROMPT}\n\n[assistant] I'll create `notes.txt`")
    assert "[tool_use: write]" in flat and "[tool_use: hashline_edit]" not in flat
    assert "[tool_result: exit: 0\nbeta\n]" in flat
    assert flat.endswith("[assistant] DONE")
    assert "You are Grok" not in flat  # the system prompt is not the session


def test_events_carry_session_cwd_and_times(tmp_path: Path):
    events = gt.load_events(write_session(tmp_path / ".grok"))

    assert {e["sessionId"] for e in events} == {SESSION_ID}
    assert {e["cwd"] for e in events} == {CWD}
    assert events[0]["timestamp"] == "2026-10-16T12:58:06.700000Z"  # summary.created_at
    assert events[-1]["timestamp"] == "2026-10-16T12:58:19.181318Z"  # summary.updated_at
    assert briefing._compute_duration_minutes(events) == 0
    turn = user_turn_records(events)[0]
    assert turn.uuid == f"{SESSION_ID}:3" and turn.timestamp == "2026-10-16T12:58:06.852575Z"


def test_a_session_with_no_prompt_time_takes_the_summarys_start(tmp_path: Path):
    history = write_session(tmp_path / ".grok", prompt_times={},
                            created_at="2026-10-16T12:00:00Z", updated_at="2026-10-16T12:45:30.5+00:00")

    events = gt.load_events(history)

    assert events[0]["timestamp"] == "2026-10-16T12:00:00.000000Z"
    assert events[-1]["timestamp"] == "2026-10-16T12:45:30.500000Z"
    assert briefing._compute_duration_minutes(events) == 45


def test_iso_folds_nanoseconds_and_offsets_into_one_shape():
    assert gt._iso("2026-10-16T12:57:26.097Z") == "2026-10-16T12:57:26.097000Z"
    assert gt._iso("2026-10-16T12:57:26.123456789Z") == "2026-10-16T12:57:26.123456Z"
    assert gt._iso("2026-10-16T09:57:26-03:00") == "2026-10-16T12:57:26.000000Z"
    assert gt._iso("yesterday") is None and gt._iso(None) is None


def test_the_cwd_comes_from_the_directory_when_summary_has_none(tmp_path: Path):
    history = write_session(tmp_path / ".grok", summary_cwd=False)

    assert {e["cwd"] for e in gt.load_events(history)} == {CWD}


def test_bad_lines_are_skipped_and_a_missing_file_is_empty(tmp_path: Path):
    history = write_session(tmp_path / ".grok", records=[])
    history.write_text(
        "not json\n[1, 2]\n\n"
        + json.dumps(_user_record("<user_query>\nhi\n</user_query>", prompt_index=0)) + "\n"
        + '{"type": "assistant", "content": "", "tool_calls": [{"name": "write", "arguments": "{oops"}]}\n',
        encoding="utf-8",
    )

    events = gt.load_events(history)

    assert user_turns(events) == ["hi"]
    assert events[-1]["message"]["content"][0]["input"] == {"arguments": "{oops"}
    assert gt.load_events(tmp_path / "nowhere" / "chat_history.jsonl") == []


def test_updates_jsonl_reads_as_the_history_beside_it(tmp_path: Path):
    history = write_session(tmp_path / ".grok")
    updates = history.with_name("updates.jsonl")

    assert gt.is_grok_history(updates)
    assert gt.load_events(updates) == gt.load_events(history)
    assert gt.grok_session_id(updates) == SESSION_ID


def test_is_grok_history_never_takes_a_claude_transcript(tmp_path: Path):
    claude = tmp_path / ".claude" / "projects" / "-Users-you-proj" / f"{SESSION_ID}.jsonl"
    claude.parent.mkdir(parents=True)
    claude.write_text("{}\n", encoding="utf-8")
    lone_updates = tmp_path / "x" / "updates.jsonl"

    assert gt.is_grok_history(tmp_path / "s" / "chat_history.jsonl")
    assert not gt.is_grok_history(claude)
    assert not gt.is_grok_history(lone_updates)
    assert gt.grok_session_id(claude) is None


def test_session_id_is_the_directory_not_the_stem(tmp_path: Path):
    history = write_session(tmp_path / ".grok")

    assert gt.grok_session_id(history) == SESSION_ID
    assert briefing.session_id_for(history) == SESSION_ID


def test_user_query_unwraps_every_block_and_ignores_framing():
    framed = "Interjected mid-turn:\n<user_query>\n  use spaces \n</user_query>\nUnfinished tasks: …"

    assert gt.user_query(framed) == "use spaces"
    assert gt.user_query("<user_query>\n\n</user_query>") is None
    assert gt.user_query("plain text, no envelope") is None
