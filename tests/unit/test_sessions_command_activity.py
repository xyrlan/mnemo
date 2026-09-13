"""`mnemo sessions` reading activity, and `--watch` not re-reading from zero.

The offset dict is the whole point of the watch path: without it every tick
re-reads a 1-3.6MB transcript per child.
"""
from __future__ import annotations

import argparse
import json

import pytest

from mnemo.cli.commands.sessions import cmd_sessions


def _args(**kw):
    kw.setdefault("json", False)
    kw.setdefault("watch", False)
    kw.setdefault("all", True)
    kw.setdefault("consume_unblocks", False)
    return argparse.Namespace(**kw)


@pytest.fixture
def transcript(tmp_path):
    p = tmp_path / "child.jsonl"
    p.write_text(json.dumps({
        "type": "assistant",
        "timestamp": "2026-09-13T14:02:11.000Z",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t", "name": "Edit",
             "input": {"file_path": "/r/dispatch.py"}}]},
    }) + "\n")
    return p


@pytest.fixture
def one_session(monkeypatch, transcript):
    from mnemo.core.sessions.jobs import Session

    session = Session(short_id="abc", tempo="active", name="child",
                      link_scan_path=str(transcript))
    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions",
                        lambda **kw: [session])
    monkeypatch.setattr("mnemo.core.sessions.detector.sweep",
                        lambda *a, **kw: None)
    return session


def test_single_invocation_shows_activity(one_session, capsys):
    assert cmd_sessions(_args()) == 0

    out = capsys.readouterr().out
    assert "Edit dispatch.py" in out


def test_json_output_is_unchanged_by_activity(one_session, capsys):
    """--json is a Session dump; activity is a render concern, not a field."""
    assert cmd_sessions(_args(json=True)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["short_id"] == "abc"
    assert "activity" not in payload[0]


def test_watch_does_not_reread_from_zero(one_session, transcript, monkeypatch, capsys):
    """Second tick must read only the delta. This is the measured cost fix."""
    reads = []
    real = __import__("mnemo.core.activity.tail", fromlist=["read_tail"]).read_tail

    def spy(path, offset, window=None):
        reads.append(offset)
        return real(path, offset) if window is None else real(path, offset, window)

    monkeypatch.setattr("mnemo.core.activity.read_tail", spy)

    ticks = [0]

    def fake_sleep(_):
        ticks[0] += 1
        if ticks[0] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", fake_sleep)

    assert cmd_sessions(_args(watch=True)) == 0

    assert len(reads) >= 2, reads
    assert reads[0] == 0, "first tick is a cold start"
    assert reads[1] > 0, "second tick must resume from the bookmark"


def test_watch_keeps_showing_activity_when_nothing_changes(
        one_session, monkeypatch, capsys):
    """A quiet child must not blink to em-dash on the second tick."""
    ticks = [0]

    def fake_sleep(_):
        ticks[0] += 1
        if ticks[0] >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", fake_sleep)

    assert cmd_sessions(_args(watch=True)) == 0

    out = capsys.readouterr().out
    assert out.count("Edit dispatch.py") >= 2, out


def test_session_without_a_transcript_still_renders(monkeypatch, capsys):
    from mnemo.core.sessions.jobs import Session

    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions",
                        lambda **kw: [Session(short_id="abc", tempo="active",
                                              name="child", detail="building")])
    monkeypatch.setattr("mnemo.core.sessions.detector.sweep", lambda *a, **kw: None)

    assert cmd_sessions(_args()) == 0

    assert "building" in capsys.readouterr().out


def test_activity_failure_never_breaks_the_queue(monkeypatch, capsys):
    """The queue must print even when the activity read explodes."""
    from mnemo.core.sessions.jobs import Session

    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions",
                        lambda **kw: [Session(short_id="abc", tempo="active",
                                              name="child", detail="building")])
    monkeypatch.setattr("mnemo.core.sessions.detector.sweep", lambda *a, **kw: None)

    def boom(*a, **kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("mnemo.cli.commands.sessions.activities_for", boom, raising=False)

    assert cmd_sessions(_args()) == 0
    assert "building" in capsys.readouterr().out
