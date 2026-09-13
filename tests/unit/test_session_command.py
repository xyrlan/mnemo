"""`mnemo session <short_id>` — the detail view.

Layer 2: you look, you decide, you go to `claude attach` if you want in.
No --follow in this version.
"""
from __future__ import annotations

import argparse
import json

import pytest

from mnemo.cli.commands.session import cmd_session


def _args(short_id="abc", limit=15):
    return argparse.Namespace(short_id=short_id, limit=limit)


def _event(tool, key, value, ts="2026-09-13T14:02:11.000Z"):
    return json.dumps({
        "type": "assistant",
        "timestamp": ts,
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t", "name": tool, "input": {key: value}}]},
    })


@pytest.fixture
def session_with_actions(monkeypatch, tmp_path):
    from mnemo.core.sessions.jobs import Session

    p = tmp_path / "child.jsonl"
    p.write_text("\n".join([
        _event("Grep", "pattern", "linkScanOffset", "2026-09-13T14:02:11.000Z"),
        _event("Read", "file_path", "/r/detector.py", "2026-09-13T14:02:19.000Z"),
        _event("Edit", "file_path", "/r/detector.py", "2026-09-13T14:03:02.000Z"),
        _event("Bash", "description", "Run tests", "2026-09-13T14:03:40.000Z"),
        _event("Grep", "pattern", "linkScanOffset", "2026-09-13T14:04:15.000Z"),
    ]) + "\n")

    session = Session(short_id="abc", tempo="active", state="active", name="measure-edges",
                      cwd="/Users/x/github/mnemo-wt-203", live=True,
                      link_scan_path=str(p))
    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions", lambda **kw: [session])
    return session


def test_lists_actions_oldest_first(session_with_actions, capsys):
    assert cmd_session(_args()) == 0

    out = capsys.readouterr().out
    assert out.index("Grep") < out.index("Read") < out.index("Bash")


def test_shows_the_header_with_label_and_cwd(session_with_actions, capsys):
    assert cmd_session(_args()) == 0

    out = capsys.readouterr().out
    assert "measure-edges" in out
    assert "mnemo-wt-203" in out


def test_shows_tool_and_target_per_action(session_with_actions, capsys):
    assert cmd_session(_args()) == 0

    out = capsys.readouterr().out
    assert "detector.py" in out
    assert "Run tests" in out


def test_marks_a_repeated_action(session_with_actions, capsys):
    """The same grep twice is the loop signal, and it is why you look here."""
    assert cmd_session(_args()) == 0

    assert "↻" in capsys.readouterr().out


def test_limit_is_respected(session_with_actions, capsys):
    assert cmd_session(_args(limit=2)) == 0

    out = capsys.readouterr().out
    assert "Bash" in out, "the newest actions are the ones kept"
    assert "Read" not in out


def test_unknown_short_id_reports_and_fails(monkeypatch, capsys):
    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions", lambda **kw: [])

    assert cmd_session(_args(short_id="nope")) == 1

    assert "nope" in capsys.readouterr().out


def test_session_without_a_transcript_says_so(monkeypatch, capsys):
    from mnemo.core.sessions.jobs import Session

    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions",
                        lambda **kw: [Session(short_id="abc", name="child")])

    assert cmd_session(_args()) == 0

    out = capsys.readouterr().out
    assert "abc" in out
    assert "transcript" in out.lower()


def test_transcript_with_no_tool_use_says_so(monkeypatch, tmp_path, capsys):
    from mnemo.core.sessions.jobs import Session

    p = tmp_path / "c.jsonl"
    p.write_text(json.dumps({
        "type": "assistant", "timestamp": "2026-09-13T14:00:00.000Z",
        "message": {"role": "assistant", "content": [
            {"type": "text", "text": "thinking about it"}]},
    }) + "\n")
    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions",
                        lambda **kw: [Session(short_id="abc", name="child",
                                              link_scan_path=str(p))])

    assert cmd_session(_args()) == 0

    assert "nenhuma ação" in capsys.readouterr().out


def test_prefix_match_on_short_id(session_with_actions, capsys):
    """Typing four characters of a short id is enough."""
    assert cmd_session(_args(short_id="ab")) == 0

    assert "measure-edges" in capsys.readouterr().out


def test_an_ambiguous_prefix_refuses_rather_than_guessing(monkeypatch, capsys):
    from mnemo.core.sessions.jobs import Session

    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions",
                        lambda **kw: [Session(short_id="abc1", name="one"),
                                      Session(short_id="abc2", name="two")])

    assert cmd_session(_args(short_id="abc")) == 1

    out = capsys.readouterr().out
    assert "one" not in out and "two" not in out


def test_the_loop_mark_catches_a_non_adjacent_repeat(session_with_actions, capsys):
    """Wider than `Activity.repeated`, on purpose.

    `summarize.Activity.repeated` flags only back-to-back tool uses. A real
    loop rarely is: it is grep, read, think, grep again. The fixture here
    interleaves Read/Edit/Bash between two identical Greps, so every
    `repeated` is False — and both Greps must still be marked.
    """
    from mnemo.core.activity.summarize import recent_actions

    assert cmd_session(_args()) == 0
    out = capsys.readouterr().out

    grep_lines = [l for l in out.splitlines() if "Grep" in l]
    assert len(grep_lines) == 2
    assert grep_lines[1].rstrip().endswith("↻"), grep_lines
    assert not grep_lines[0].rstrip().endswith("↻"), "the first sighting is not a loop"


def test_the_mark_stays_rare_on_ordinary_work(capsys, monkeypatch, tmp_path):
    """Measured on 9 real dispatch children: 10 of 135 shown actions (7%).

    A mark on every other line would be noise rather than signal, so this
    pins that distinct work goes unmarked.
    """
    from mnemo.core.sessions.jobs import Session

    p = tmp_path / "c.jsonl"
    p.write_text("\n".join(
        _event("Edit", "file_path", "/r/f%d.py" % i, "2026-09-13T14:0%d:00.000Z" % i)
        for i in range(6)
    ) + "\n")
    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions",
                        lambda **kw: [Session(short_id="abc", name="child",
                                              link_scan_path=str(p))])

    assert cmd_session(_args()) == 0

    assert "↻" not in capsys.readouterr().out
