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

import pytest

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

    row = next(line for line in out.splitlines() if "a1" in line and "DONE (" not in line)
    assert row.endswith("64k  32u/+107k")


def test_a_row_with_no_edit_is_marked_as_a_floor() -> None:
    out = render_queue([_done("a1")], explorations={"a1": Exploration(uses=9, tokens=500)})
    assert "≥9u/+500" in out


def test_working_rows_never_get_the_column() -> None:
    """A running child's count is still moving; its activity column says what it is doing."""
    working = Session(short_id="w1", state="working", tempo="active", name="w1")
    out = render_queue([working], explorations={"w1": Exploration(uses=5, tokens=1000, reached=True)})
    assert "5u/" not in out
    assert "before first edit" not in out


def test_the_total_sums_measured_rows_and_says_how_many() -> None:
    found = [_done("a1"), _done("a2"), _done("a3")]
    out = render_queue(found, explorations={
        "a1": Exploration(uses=20, tokens=40_000, reached=True),
        "a2": Exploration(uses=12, tokens=10_000, reached=True),
    })
    assert "before first edit (u/+tokens): 32 uses, +50k across 2 done sessions" in out


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
    assert ("before first edit: 2 uses, +21k tokens (base 70k) · "
            "2 reflex rules in the opening prompt → Edit x.py") in out


def test_session_without_an_edit_says_so(monkeypatch, tmp_path, capsys) -> None:
    path = _transcript(tmp_path, _turn("Read", {"file_path": f"{WT}/a"}, 500))
    session = Session(short_id="abc", state="working", tempo="active", cwd=WT, link_scan_path=path)
    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions", lambda **kw: [session])

    assert cmd_session(argparse.Namespace(short_id="abc", limit=15)) == 0

    assert "no edit yet: 1 use, +0 tokens (base 500) · 0 reflex rules" in capsys.readouterr().out


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


def test_tool_reports_the_ceiling_a_repo_map_could_reach(tmp_path) -> None:
    """#382: what the window was spent on, and how big an A/B would have to be."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools import measure_exploration as tool

    def child(directory: str, wt: str, *commands: str, growth: int):
        d = tmp_path / directory
        d.mkdir()
        cwd = f"/Users/x/github/{wt}"
        events = []
        for index, command in enumerate(commands):
            use = f"u{index}"
            events.append({**_turn("Bash", {"command": command}, 10_000), "cwd": cwd})
            events[-1]["message"]["content"][0]["id"] = use
            events.append({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": use, "content": "x" * 400}]}})
        events.append({**_turn("Edit", {"file_path": f"{cwd}/a"}, 10_000 + growth), "cwd": cwd})
        (d / "s.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    child("-a-mnemo-wt-1", "mnemo-wt-1", "ls src", "cat src/a.py", growth=8_000)
    child("-a-mnemo-wt-2", "mnemo-wt-2", 'grep -rn "x" src/', "cat src/b.py", growth=4_000)
    # Never mutated: its split is a floor, so it is not in the shares.
    (tmp_path / "-a-mnemo-wt-3").mkdir()
    (tmp_path / "-a-mnemo-wt-3" / "s.jsonl").write_text(json.dumps(
        {**_turn("Bash", {"command": "ls"}, 1), "cwd": "/Users/x/github/mnemo-wt-3"}) + "\n",
        encoding="utf-8")

    report = tool.measure_kinds(str(tmp_path))

    assert report["n"] == 2
    assert report["by_kind"]["list"] == {"uses": 1.0, "chars": 400.0, "answerable": True}
    assert report["by_kind"]["read"]["uses"] == 2.0
    assert report["by_kind"]["search"]["answerable"] is True
    assert report["by_kind"]["read"]["answerable"] is False
    # One of two uses is answerable; its result is 100 tokens of the 200 the
    # two returned, and it carries half of whatever the growth does not explain.
    assert report["rows"][0]["ceiling_tokens"] == pytest.approx(100 + (8_000 - 200) / 2)
    assert report["median_floor_tokens"] == 100.0

    out = tool.format_kinds(report, listing=True)
    assert "mnemo-wt-1" in out and "list=1.0" in out
    assert "por braço" in out


def test_tool_says_so_when_no_child_reached_a_mutation() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools import measure_exploration as tool

    assert "nenhum transcript" in tool.format_kinds({"rows": [], "n": 0})
