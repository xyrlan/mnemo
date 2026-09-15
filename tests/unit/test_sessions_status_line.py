"""The line a session's row says is the tool call, not Claude Code's guess (#293).

``detail`` is written by Claude Code's daemon, not by mnemo. On child
``0f6589d7`` (2026-09-15) its timeline read, for ~20 minutes::

    17:23:25  awaiting task clarification or file boundaries
    17:27:29  awaiting task specification; message truncated

while the transcript's last tool use, replayed through ``read_tail`` +
``summarize`` at those instants, was a ``Bash`` from four seconds earlier.
The table already preferred the tool call; ``--json`` — what mnemo-desktop
reads — had only ``detail``. These pin one rule for both.
"""
from __future__ import annotations

import argparse
import json

from mnemo.cli.commands.sessions import cmd_sessions
from mnemo.core.activity import Activity
from mnemo.core.sessions.jobs import Session
from mnemo.core.sessions.render import render_queue, status_line

WRONG = "awaiting task specification; message truncated"


def _tool_use(name: str, input_: dict, at: str) -> bytes:
    return json.dumps({
        "type": "assistant",
        "timestamp": at,
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": at, "name": name, "input": input_}]},
    }).encode("utf-8") + b"\n"


def _child(tmp_path, **kw) -> Session:
    transcript = tmp_path / "child.jsonl"
    # Bytes, so Windows does not rewrite the newlines under the offsets.
    transcript.write_bytes(
        _tool_use("Edit", {"file_path": "/wt/src/mnemo/core/briefing_select.py"},
                  "2026-09-15T17:27:10.000Z")
        + _tool_use("Bash", {"command": "PYTHONPATH=src python3 -m pytest",
                             "description": "Run briefing_select tests"},
                    "2026-09-15T17:27:25.942Z")
    )
    fields = dict(short_id="0f6589d7", state="working", tempo="active", live=True,
                  name="channels briefing-query implementation", detail=WRONG,
                  link_scan_path=str(transcript),
                  updated_at="2026-09-15T17:27:29.316Z")
    fields.update(kw)
    return Session(**fields)


def _json(monkeypatch, capsys, found) -> list[dict]:
    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions", lambda **kw: found)
    monkeypatch.setattr("mnemo.core.sessions.detector.sweep", lambda *a, **kw: None)
    args = argparse.Namespace(json=True, watch=False, consume_unblocks=False, **{"all": True})
    assert cmd_sessions(args) == 0
    return json.loads(capsys.readouterr().out)


def test_json_status_line_is_the_tool_call_not_the_stale_summary(tmp_path, monkeypatch, capsys):
    (row,) = _json(monkeypatch, capsys, [_child(tmp_path)])

    # (+1): one tool use before it in the window, as the table marks it.
    assert row["status_line"] == "Bash Run briefing_select tests (+1)"
    assert row["activity"]["at"] == "2026-09-15T17:27:25.942Z"
    # The raw field stays: additive, as every --json change has been.
    assert row["detail"] == WRONG


def test_a_summary_newer_than_the_tool_call_still_loses(tmp_path):
    """Freshness is not the rule: the wrong line above was the *newer* one."""
    s = _child(tmp_path)
    act = Activity(tool="Bash", target="Run briefing_select tests",
                   at="2026-09-15T17:27:25.942Z")

    assert s.updated_at > act.at
    assert status_line(s, act) == "Bash Run briefing_select tests"


def test_the_table_and_json_say_the_same_thing(tmp_path, monkeypatch, capsys):
    s = _child(tmp_path)
    (row,) = _json(monkeypatch, capsys, [s])
    act = Activity(**row["activity"])

    table = render_queue([s], {s.short_id: act})

    # Same rule, the table's column budget applied on top of it.
    assert status_line(s, act, budget=None) == row["status_line"]
    assert status_line(s, act) in table
    assert status_line(s, act).startswith("Bash Run briefing_select")
    assert WRONG not in table


def test_detail_is_the_fallback_when_no_tool_call_is_known(tmp_path, monkeypatch, capsys):
    s = _child(tmp_path, link_scan_path=None, detail="building briefing-query")

    (row,) = _json(monkeypatch, capsys, [s])

    assert row["activity"] is None
    assert row["status_line"] == "building briefing-query"


def test_a_waiting_session_says_what_it_needs(tmp_path):
    """The claim on the maintainer outranks what the child last ran."""
    s = _child(tmp_path, state="blocked", tempo="blocked", needs="which branch?")
    act = Activity(tool="Bash", target="git status")

    assert status_line(s, act) == "which branch?"


def test_a_finished_session_says_its_result(tmp_path, monkeypatch, capsys):
    """``detail`` on a done session is the final result — keep it, skip the read."""
    s = _child(tmp_path, state="done", tempo="idle",
               detail="briefing-query: 26 tests, suite green")

    (row,) = _json(monkeypatch, capsys, [s])

    assert row["activity"] is None
    assert row["status_line"] == "briefing-query: 26 tests, suite green"


def test_json_leaves_a_long_target_whole(tmp_path):
    """The table budgets to its column; a JSON consumer has its own layout."""
    s = _child(tmp_path)
    act = Activity(tool="Bash", target="x" * 40, since=12)

    assert status_line(s, act, budget=None) == "Bash " + "x" * 40 + " (+12)"
    assert len(status_line(s, act)) <= 34


def test_nothing_known_is_none(tmp_path):
    assert status_line(_child(tmp_path, detail=None)) is None
