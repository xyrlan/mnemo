"""Where the pre-edit exploration number is shown (#269).

``mnemo sessions`` gives it one column on finished rows and a total under the
table; ``mnemo session <id>`` gives it one line; ``--json`` carries it per
session so a dispatch's total is a sum a consumer can take. The measurement
itself is tested in ``test_activity_exploration.py``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mnemo.cli.commands import sessions as sessions_cmd
from mnemo.cli.commands.session import cmd_session
from mnemo.core.activity.exploration import Exploration
from mnemo.core.sessions.jobs import Session
from mnemo.core.sessions.render import render_queue

WT = "/Users/x/github/mnemo-wt-269"


def _done(short_id: str, **kw) -> Session:
    return Session(short_id=short_id, state="done", tempo="idle", name=short_id, **kw)


# --- the queue ----------------------------------------------------------------

def test_omitting_explorations_leaves_the_queue_byte_identical() -> None:
    found = [_done("a1"), Session(short_id="w1", state="working", tempo="active")]
    assert render_queue(found) == render_queue(found, explorations={}) == render_queue(found, explorations=None)


def test_a_finished_row_ends_with_uses_and_growth() -> None:
    out = render_queue([_done("a1", context_tokens=64_000)],
                       explorations={"a1": Exploration(uses=32, tokens=107_400, baseline=70_000, reached=True)})

    row = next(line for line in out.splitlines() if "a1" in line and "PRONTAS" not in line)
    assert row.endswith("64k  32u/+107k")


def test_a_row_with_no_edit_is_marked_as_a_floor() -> None:
    out = render_queue([_done("a1")], explorations={"a1": Exploration(uses=9, tokens=500)})
    assert "≥9u/+500" in out


def test_working_rows_never_get_the_column() -> None:
    """A running child's count is still moving; its activity column says what it is doing."""
    working = Session(short_id="w1", state="working", tempo="active", name="w1")
    out = render_queue([working], explorations={"w1": Exploration(uses=5, tokens=1000, reached=True)})
    assert "5u/" not in out
    assert "antes da 1ª edição" not in out


def test_the_total_sums_measured_rows_and_says_how_many() -> None:
    found = [_done("a1"), _done("a2"), _done("a3")]
    out = render_queue(found, explorations={
        "a1": Exploration(uses=20, tokens=40_000, reached=True),
        "a2": Exploration(uses=12, tokens=10_000, reached=True),
    })
    assert "antes da 1ª edição (u/+tokens): 32 usos, +50k em 2 sessões prontas" in out


# --- the detail view ----------------------------------------------------------

def _transcript(tmp_path: Path, *events) -> str:
    path = tmp_path / "child.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return str(path)


def _turn(name, input_, context):
    return {"type": "assistant", "timestamp": "2026-09-14T10:00:00.000Z", "message": {
        "role": "assistant",
        "usage": {"input_tokens": context},
        "content": [{"type": "tool_use", "id": "t", "name": name, "input": input_}],
    }}


def test_session_prints_the_exploration_line(monkeypatch, tmp_path, capsys) -> None:
    reflex = {"type": "attachment", "attachment": {
        "type": "hook_additional_context",
        "content": ["mnemo reflex context:\n• [[mnemo__a]]: x\n• [[mnemo__b]]: y"]}}
    path = _transcript(
        tmp_path, reflex,
        _turn("Bash", {"command": "gh issue view 269"}, 70_000),
        _turn("Read", {"file_path": f"{WT}/src/x.py"}, 80_000),
        _turn("Edit", {"file_path": f"{WT}/src/x.py"}, 91_000),
    )
    session = Session(short_id="abc", state="done", tempo="idle", cwd=WT, link_scan_path=path)
    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions", lambda **kw: [session])

    assert cmd_session(argparse.Namespace(short_id="abc", limit=15)) == 0

    out = capsys.readouterr().out
    assert ("antes da 1ª edição: 2 usos, +21k tokens (base 70k) · "
            "2 regras do reflex no prompt de abertura → Edit x.py") in out


def test_session_without_an_edit_says_so(monkeypatch, tmp_path, capsys) -> None:
    path = _transcript(tmp_path, _turn("Read", {"file_path": f"{WT}/a"}, 500))
    session = Session(short_id="abc", state="working", tempo="active", cwd=WT, link_scan_path=path)
    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions", lambda **kw: [session])

    assert cmd_session(argparse.Namespace(short_id="abc", limit=15)) == 0

    assert "ainda sem edição: 1 usos, +0 tokens (base 500) · 0 regras" in capsys.readouterr().out


# --- the command --------------------------------------------------------------

def _patch_queue(monkeypatch, found):
    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions",
                        lambda root=None, *, cwd=None: found)
    monkeypatch.setattr("mnemo.core.sessions.detector.sweep",
                        lambda sessions, *, vault_root: 0)


def test_json_carries_the_exploration_per_session(monkeypatch, tmp_path, capsys) -> None:
    path = _transcript(tmp_path, _turn("Read", {"file_path": "x"}, 100),
                       _turn("Write", {"file_path": f"{WT}/y"}, 160))
    found = [
        Session(short_id="work", state="working", tempo="active", cwd=WT, link_scan_path=path),
        Session(short_id="none", state="done", tempo="idle"),
    ]
    _patch_queue(monkeypatch, found)

    assert sessions_cmd.cmd_sessions(argparse.Namespace(json=True, watch=False, **{"all": True})) == 0

    rows = {r["short_id"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["work"]["exploration"] == {
        "uses": 1, "tokens": 60, "baseline": 100, "reached": True,
        "tool": "Write", "target": "y", "injected": None,
    }
    assert rows["none"]["exploration"] is None


def test_the_queue_reads_a_finished_transcript_once_per_process(monkeypatch, tmp_path) -> None:
    calls = []

    def fake(path, cwd=None):
        calls.append(path)
        return Exploration(uses=1, reached=True)

    monkeypatch.setattr("mnemo.core.activity.exploration_for", fake)
    found = [_done("a1", link_scan_path="/t/a1.jsonl"),
             Session(short_id="w1", state="working", tempo="active", link_scan_path="/t/w1.jsonl")]
    cache: dict = {}

    for _ in range(3):
        out = sessions_cmd._explorations(found, cache)

    assert set(out) == {"a1"}
    assert calls == ["/t/a1.jsonl"]


def test_a_transcript_that_raises_costs_its_cell_not_the_queue(monkeypatch) -> None:
    def boom(path, cwd=None):
        raise RuntimeError("unreadable")

    monkeypatch.setattr("mnemo.core.activity.exploration_for", boom)
    assert sessions_cmd._explorations([_done("a1", link_scan_path="/t")], {}) == {}


# --- the measurement tool -----------------------------------------------------

def test_tool_splits_children_by_injected_rules(tmp_path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools import measure_exploration as tool

    def child(directory: str, wt: str, rules: int, uses: int):
        d = tmp_path / directory
        d.mkdir()
        cwd = f"/Users/x/github/{wt}"
        events = []
        if rules:
            bullets = "\n".join(f"• [[mnemo__r{i}]]: p" for i in range(rules))
            events.append({"type": "attachment", "cwd": cwd, "attachment": {
                "type": "hook_additional_context", "content": [f"mnemo reflex context:\n{bullets}"]}})
        for i in range(uses):
            events.append({**_turn("Read", {"file_path": "a"}, 1000 + i), "cwd": cwd})
        events.append({**_turn("Edit", {"file_path": f"{cwd}/a"}, 5000), "cwd": cwd})
        (d / "s.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    child("-a-mnemo-wt-1", "mnemo-wt-1", 0, 10)
    child("-a-mnemo-wt-2", "mnemo-wt-2", 0, 20)
    child("-a-mnemo-wt-3", "mnemo-wt-3", 2, 4)
    child("-a-plain", "mnemo", 0, 99)  # not a dispatch worktree
    pytest_tree = tmp_path / "-p-pytest-of-x-live-wt-1"
    pytest_tree.mkdir()
    (pytest_tree / "s.jsonl").write_text(json.dumps(
        {**_turn("Read", {"file_path": "a"}, 1), "cwd": "/private/var/pytest-of-x/t0/live-wt-1"}) + "\n",
        encoding="utf-8")

    report = tool.measure(str(tmp_path))

    assert report["transcripts"] == 3
    assert report["by_injection"]["0 regras"] == {"n": 2, "median_uses": 15.0, "median_tokens": 4000.0}
    assert report["by_injection"]["2 regras"]["n"] == 1
    assert "n=3" in tool.format_report(report)
