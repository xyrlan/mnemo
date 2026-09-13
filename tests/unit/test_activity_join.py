"""Joining a Session to the transcript its state.json points at.

`linkScanPath` has been parsed into Session.link_scan_path since the queue
shipped (jobs.py:133) and nothing ever opened it. These tests are that open.
"""
from __future__ import annotations

import json

from mnemo.core.activity import activities_for, activity_for
from mnemo.core.sessions.jobs import Session


def _transcript(tmp_path, name, tools):
    p = tmp_path / name
    lines = []
    for tool, target_key, target in tools:
        lines.append(json.dumps({
            "type": "assistant",
            "timestamp": "2026-09-13T14:02:11.000Z",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t", "name": tool, "input": {target_key: target}},
            ]},
        }))
    # Bytes, not write_text: on Windows the latter turns each "\n" into
    # "\r\n", which shifts every byte offset this file asserts on. Claude
    # Code writes transcripts with bare LF.
    p.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return str(p)


def test_reads_the_transcript_named_by_link_scan_path(tmp_path):
    path = _transcript(tmp_path, "a.jsonl", [("Edit", "file_path", "/r/dispatch.py")])
    session = Session(short_id="abc", link_scan_path=path)

    act, offset = activity_for(session, 0)

    assert act.tool == "Edit"
    assert act.target == "dispatch.py"
    assert offset > 0


def test_no_link_scan_path_gives_no_activity(tmp_path):
    act, offset = activity_for(Session(short_id="abc"), 0)

    assert act is None
    assert offset == 0


def test_missing_file_gives_no_activity(tmp_path):
    session = Session(short_id="abc", link_scan_path=str(tmp_path / "gone.jsonl"))

    act, offset = activity_for(session, 0)

    assert act is None
    assert offset == 0


def test_offset_advances_and_second_read_sees_only_new_work(tmp_path):
    path = _transcript(tmp_path, "a.jsonl", [("Read", "file_path", "/r/a.py")])
    session = Session(short_id="abc", link_scan_path=path)
    first, offset = activity_for(session, 0)
    assert first.tool == "Read"

    with open(path, "ab") as fh:
        fh.write(json.dumps({
            "type": "assistant", "timestamp": "2026-09-13T14:03:00.000Z",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t2", "name": "Bash",
                 "input": {"description": "Run tests"}}]},
        }).encode("utf-8") + b"\n")
    second, new_offset = activity_for(session, offset)

    assert second.tool == "Bash"
    assert second.target == "Run tests"
    assert new_offset > offset


def test_no_new_events_returns_none_and_holds_the_offset(tmp_path):
    """The caller keeps showing the previous activity; see sessions.py."""
    path = _transcript(tmp_path, "a.jsonl", [("Read", "file_path", "/r/a.py")])
    session = Session(short_id="abc", link_scan_path=path)
    _, offset = activity_for(session, 0)

    act, new_offset = activity_for(session, offset)

    assert act is None
    assert new_offset == offset


def test_activities_for_maps_by_short_id_and_threads_offsets(tmp_path):
    a = _transcript(tmp_path, "a.jsonl", [("Edit", "file_path", "/r/a.py")])
    b = _transcript(tmp_path, "b.jsonl", [("Bash", "description", "Build")])
    sessions = [
        Session(short_id="aaa", link_scan_path=a),
        Session(short_id="bbb", link_scan_path=b),
        Session(short_id="ccc"),  # no transcript at all
    ]
    offsets = {}

    acts = activities_for(sessions, offsets)

    assert acts["aaa"].target == "a.py"
    assert acts["bbb"].target == "Build"
    assert "ccc" not in acts
    assert offsets["aaa"] > 0 and offsets["bbb"] > 0
    assert "ccc" not in offsets


def test_activities_for_keeps_the_previous_activity_when_nothing_is_new(tmp_path):
    """A quiet session must not blink back to '—' on the next tick."""
    a = _transcript(tmp_path, "a.jsonl", [("Edit", "file_path", "/r/a.py")])
    sessions = [Session(short_id="aaa", link_scan_path=a)]
    offsets = {}

    first = activities_for(sessions, offsets)
    second = activities_for(sessions, offsets, previous=first)

    assert second["aaa"].target == "a.py"


def test_activities_for_tolerates_an_unreadable_transcript(tmp_path):
    sessions = [Session(short_id="aaa", link_scan_path=str(tmp_path))]  # a directory

    acts = activities_for(sessions, {})

    assert acts == {}
