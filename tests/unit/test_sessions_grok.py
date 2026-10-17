"""Grok children in the session readers (grok-host wave 2, grok-sessions).

The session directories here are built from the records measured on grok
1.0.60 (2026-10-16): ``events.jsonl``'s ``turn_started`` /
``permission_requested`` / ``turn_ended{outcome}``, ``updates.jsonl``'s
``turn_completed{stop_reason}``, ``summary.json`` and ``signals.json``. The
leader is never asked: the roster and the socket probe are replaced, and a
test that would reach ``grok`` fails instead.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import tempfile
import threading
import time
import types
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from mnemo.core.grok import acp, paths
from mnemo.core.sessions import grok_sessions, jobs, stalls
from mnemo.core.sessions.jobs import Session, read_sessions


@pytest.fixture
def tmp_path(short_tmp_path: Path) -> Path:
    """Short on Windows: these tests nest a percent-encoded cwd under it (#729)."""
    return short_tmp_path

SID = "01a144cc-77f5-7ea3-9060-06013224200f"
SID2 = "01a144cc-77f5-7ea3-9060-0601abcd0001"


@dataclass(frozen=True)
class ChildAgent:
    """The shape grok-spawn's ``child_agents`` ledger hands back (the contract)."""

    short_id: str
    agent: str
    session_id: str
    home: Optional[str]
    cwd: str


def _child(session_id: str, cwd: Path, home: Optional[Path] = None, agent: str = "grok") -> ChildAgent:
    return ChildAgent(paths.short_id(session_id), agent, session_id,
                      None if home is None else str(home), str(cwd))


class _Ledger(list):
    """The ledger's rows, and the vaults it was read from."""

    vaults: List[Path]


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    """A stand-in ``child_agents`` module; returns the list it reads from."""
    rows = _Ledger()
    vaults: List[Path] = []

    def read(vault_root):
        vaults.append(Path(vault_root))
        return {c.short_id: c for c in rows}

    fake = types.ModuleType("mnemo.core.sessions.child_agents")
    fake.read = read  # type: ignore[attr-defined]
    import mnemo.core.sessions as package

    monkeypatch.setitem(sys.modules, "mnemo.core.sessions.child_agents", fake)
    monkeypatch.setattr(package, "child_agents", fake, raising=False)
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(grok_sessions, "_default_vault", lambda: vault)
    rows.vaults = vaults
    return rows


@pytest.fixture
def leader(monkeypatch):
    """The leader as the tests say it is: ``state['listening']`` and ``state['rows']``."""
    state: Dict[str, Any] = {"listening": True, "rows": [], "calls": 0}

    def fetch(home):
        state["calls"] += 1
        if isinstance(state["rows"], Exception):
            raise state["rows"]
        return list(state["rows"])

    monkeypatch.setattr(grok_sessions, "leader_listening", lambda home: state["listening"])
    monkeypatch.setattr(grok_sessions, "_fetch_roster", fetch)
    return state


def _row(session_id: str, home: Path, activity: str, *, resident: Optional[bool] = None,
         model: str = "grok-4.7", effort: Optional[str] = "high",
         last_change_ms: Optional[int] = None) -> acp.RosterEntry:
    if resident is None:
        resident = activity in ("working", "idle", "needs_input")
    return acp.RosterEntry(session_id=session_id, cwd="", activity=activity, resident=resident,
                           model_id=model, effort=effort, title="roster title",
                           last_change_ms=last_change_ms, home=home)


def _session_dir(home: Path, cwd: Path, session_id: str) -> Path:
    directory = home / "sessions" / paths.encode_cwd(str(cwd)) / session_id
    directory.mkdir(parents=True)
    (directory / "chat_history.jsonl").write_text("", encoding="utf-8")
    return directory


def _jsonl(path: Path, records: List[Dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def _events(directory: Path, *kinds: Dict[str, Any]) -> None:
    records = [{"ts": "2026-10-16T13:00:13.373Z", "type": "turn_started",
                "session_id": directory.name, "turn_number": 0}]
    for kind in kinds:
        records.append({"ts": "2026-10-16T13:00:14.000Z", "type": "phase_changed",
                        "phase": "streaming_text"})
        records.append(dict({"ts": "2026-10-16T13:00:15.000Z"}, **kind))
    _jsonl(directory / "events.jsonl", records)


def _summary(directory: Path, **extra: Any) -> None:
    data = {"info": {"id": directory.name}, "created_at": "2026-10-16T13:00:13.203782Z",
            "updated_at": "2026-10-16T13:03:13.972784Z", "current_model_id": "grok-4.7",
            "reasoning_effort": "low", "generated_title": "Fix the parser",
            "last_turn_summary": "Opened PR #12"}
    data.update(extra)
    (directory / "summary.json").write_text(json.dumps(data), encoding="utf-8")


#: The three records every rate-limited turn ended with on 2026-10-16.
RATE_LIMIT_TAIL = [
    {"timestamp": 1792156849, "method": "_x.ai/session/update", "params": {
        "sessionId": SID, "update": {"sessionUpdate": "retry_state", "type": "retrying",
                                     "attempt": 1, "max_retries": 15,
                                     "reason": "API error (status 429 Too Many Requests): You are "
                                               "sending requests too quickly.",
                                     "error_type": "rate_limited"}}},
    {"timestamp": 1792156852, "method": "_x.ai/session/update", "params": {
        "sessionId": SID, "update": {"sessionUpdate": "retry_state", "type": "exhausted",
                                     "attempts": 0, "is_rate_limited": True,
                                     "reason": "API error (status 429 Too Many Requests): "
                                               "subscription:free-usage-exhausted: You've used all "
                                               "the included free usage for model grok-4.7 for "
                                               "now. Usage resets over a rolling 24-hour window"},
        "_meta": {"agentTimestampMs": 1792156852014}}},
    {"timestamp": 1792156853, "method": "_x.ai/session/update", "params": {
        "sessionId": SID, "update": {"sessionUpdate": "turn_completed", "prompt_id": "p",
                                     "stop_reason": "rate_limit", "elapsed_ms": 4510},
        "_meta": {"agentTimestampMs": 1792156853029}}},
]


@pytest.fixture
def home(tmp_path) -> Path:
    found = tmp_path / "grok-home"
    found.mkdir()
    return found


@pytest.fixture
def tree(tmp_path) -> Path:
    found = tmp_path / "repo-wt-c-parser"
    found.mkdir()
    return found


def _grok_only(**kw) -> List[Session]:
    """``read_sessions`` with no Claude account to read: the Grok rows alone."""
    return read_sessions(Path("/nonexistent-jobs"), grok=True, **kw)


# --------------------------------------------------------------------------
# Claude stays what it was


def test_no_grok_child_leaves_the_claude_list_untouched(monkeypatch, tmp_path, ledger, leader):
    jobs_root = tmp_path / "jobs"
    (jobs_root / "aaaa1111").mkdir(parents=True)
    (jobs_root / "aaaa1111" / "state.json").write_text(json.dumps(
        {"state": "working", "tempo": "active", "cwd": str(tmp_path)}), encoding="utf-8")
    from mnemo.core.sessions import accounts

    monkeypatch.setattr(accounts, "roots", lambda: [accounts.Root(home=tmp_path, jobs=jobs_root)])

    everything = read_sessions()
    claude_only = read_sessions(grok=False)

    assert everything == claude_only
    assert [s.agent for s in everything] == ["claude"]
    assert leader["calls"] == 0


def test_a_named_jobs_root_reads_no_grok_child(ledger, leader, home, tree):
    """The statusline's tests and doctor pass a root: that place alone, as before."""
    ledger.append(_child(SID, tree, home))
    assert read_sessions(Path("/nonexistent-jobs")) == []
    assert ledger.vaults == []


def test_claude_children_in_the_ledger_are_not_read_as_grok(ledger, leader, home, tree):
    ledger.append(_child(SID, tree, home, agent="claude"))
    assert _grok_only() == []


def test_the_ledger_comes_from_the_vault_named(ledger, leader, home, tree, tmp_path):
    other = tmp_path / "other-vault"
    other.mkdir()
    ledger.append(_child(SID, tree, home))
    _grok_only(vault_root=other)
    assert ledger.vaults == [other]  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# What each field says


def test_a_working_child_reads_working(ledger, leader, home, tree):
    directory = _session_dir(home, tree, SID)
    _events(directory)
    _summary(directory)
    ledger.append(_child(SID, tree, home))
    leader["rows"] = [_row(SID, home, "working", effort="medium")]

    (s,) = _grok_only()

    assert s.agent == "grok"
    assert s.short_id == "3224200f"  # the last eight hex digits, not the first
    assert s.session_id == SID
    assert (s.state, s.tempo, s.needs, s.live) == ("working", "active", None, True)
    assert (s.model, s.effort) == ("grok-4.7", "medium")  # the roster's, over summary.json's
    assert s.name == "Fix the parser"
    assert s.detail == "Opened PR #12"
    assert Path(s.cwd) == tree
    assert Path(s.link_scan_path) == directory / "chat_history.jsonl"
    assert s.label.startswith("c-parser ")


def test_needs_input_reads_blocked_and_waiting(ledger, leader, home, tree):
    directory = _session_dir(home, tree, SID)
    _events(directory, {"type": "permission_requested", "tool_name": "run_terminal_command"})
    ledger.append(_child(SID, tree, home))
    leader["rows"] = [_row(SID, home, "needs_input")]

    (s,) = _grok_only()

    assert (s.state, s.tempo, s.live) == ("blocked", "blocked", True)
    assert s.needs == "permission requested: Bash"
    assert s.is_waiting and not s.is_abandoned


def test_a_finished_child_reads_done(ledger, leader, home, tree):
    directory = _session_dir(home, tree, SID)
    _events(directory, {"type": "turn_ended", "outcome": "completed"})
    _summary(directory)
    ledger.append(_child(SID, tree, home))
    leader["rows"] = [_row(SID, home, "dormant")]

    (s,) = _grok_only()

    assert (s.state, s.tempo, s.live) == ("done", "idle", False)
    assert s.is_done and not s.is_blocked and not s.is_stale


def test_a_rate_limited_child_is_stalled_not_waiting(ledger, leader, home, tree):
    directory = _session_dir(home, tree, SID)
    _events(directory, {"type": "turn_ended", "outcome": "error"})
    _jsonl(directory / "updates.jsonl", RATE_LIMIT_TAIL)
    ledger.append(_child(SID, tree, home))
    leader["rows"] = [_row(SID, home, "dormant")]

    (s,) = _grok_only()

    assert (s.state, s.tempo, s.live) == ("blocked", "blocked", False)
    assert s.needs == "rate limited — wait and retry"
    assert s.is_abandoned  # what `mnemo resume` and the rewake watcher look for
    stall = stalls.read_stall(s)
    assert stall is not None and stall.clock_freed


def test_another_error_needs_a_person(ledger, leader, home, tree):
    directory = _session_dir(home, tree, SID)
    _events(directory, {"type": "turn_ended", "outcome": "error"})
    ledger.append(_child(SID, tree, home))
    leader["rows"] = [_row(SID, home, "dormant")]

    (s,) = _grok_only()

    assert (s.state, s.tempo) == ("blocked", "blocked")
    assert stalls.read_stall(s).kind == stalls.Kind.HUMAN


def test_a_turn_the_leader_no_longer_runs_reads_stopped(ledger, leader, home, tree):
    directory = _session_dir(home, tree, SID)
    _events(directory)  # turn_started, never ended
    ledger.append(_child(SID, tree, home))
    leader["rows"] = []  # a readable roster without it

    (s,) = _grok_only()

    assert (s.state, s.tempo, s.live) == ("stopped", "idle", False)


def test_a_permission_answered_reads_working_again(ledger, leader, home, tree):
    directory = _session_dir(home, tree, SID)
    _events(directory, {"type": "permission_requested", "tool_name": "write"},
            {"type": "permission_resolved", "tool_name": "write", "decision": "allow"})
    ledger.append(_child(SID, tree, home))
    leader["listening"] = None  # unknown: the disk is all there is

    (s,) = _grok_only()

    assert (s.state, s.tempo, s.live) == ("working", "active", None)


def test_updated_at_is_the_latest_of_the_summary_and_the_roster(ledger, leader, home, tree):
    directory = _session_dir(home, tree, SID)
    _summary(directory)
    ledger.append(_child(SID, tree, home))
    # 2026-10-16T13:10:00Z, after summary.json's 13:03:13
    leader["rows"] = [_row(SID, home, "working", last_change_ms=1792156200000)]

    (s,) = _grok_only()

    assert s.updated_at == "2026-10-16T13:10:00.000000Z"


def test_fields_grok_has_no_equivalent_for_stay_empty(ledger, leader, home, tree):
    directory = _session_dir(home, tree, SID)
    _events(directory)
    ledger.append(_child(SID, tree, home))
    leader["rows"] = [_row(SID, home, "working")]

    (s,) = _grok_only()

    assert s.tokens is None and s.intent is None and s.suggested_reply is None
    assert s.children == () and s.context_breakdown is None


# --------------------------------------------------------------------------
# Leader down, home gone, roster slow


def test_a_down_leader_is_never_started_and_the_child_still_appears(ledger, leader, home, tree):
    directory = _session_dir(home, tree, SID)
    _events(directory, {"type": "turn_ended", "outcome": "completed"})
    _summary(directory)
    ledger.append(_child(SID, tree, home))
    leader["listening"] = False

    (s,) = _grok_only()

    assert leader["calls"] == 0
    assert (s.state, s.live, s.model, s.effort) == ("done", False, "grok-4.7", "low")


def test_a_gone_home_still_lists_the_child(ledger, leader, tmp_path, tree):
    ledger.append(_child(SID, tree, tmp_path / "no-such-home"))

    (s,) = _grok_only()

    assert leader["calls"] == 0
    assert s.short_id == paths.short_id(SID)
    assert (s.state, s.live, s.link_scan_path) == ("stopped", False, None)


def test_an_unreadable_roster_proves_nothing(ledger, leader, home, tree):
    directory = _session_dir(home, tree, SID)
    _events(directory, {"type": "permission_requested", "tool_name": "write"})
    ledger.append(_child(SID, tree, home))
    leader["rows"] = acp.GrokUnavailable("leader down")

    (s,) = _grok_only()

    assert s.live is None
    assert s.is_waiting  # never accused of being abandoned on no evidence


def test_a_slow_roster_costs_its_rows_live_not_the_read(monkeypatch, ledger, leader, home, tree):
    _session_dir(home, tree, SID)
    ledger.append(_child(SID, tree, home))
    monkeypatch.setattr(grok_sessions, "ROSTER_TIMEOUT", 0.05)
    release = threading.Event()  # time.sleep is a no-op in this suite
    monkeypatch.setattr(grok_sessions, "_fetch_roster", lambda h: release.wait(5) and [])

    started = time.monotonic()
    try:
        (s,) = _grok_only()
    finally:
        release.set()

    assert time.monotonic() - started < 2
    assert s.live is None


def test_no_roster_when_every_tree_is_gone(ledger, leader, home, tmp_path):
    gone = tmp_path / "repo-wt-1"
    directory = _session_dir(home, gone, SID)
    _events(directory, {"type": "turn_ended", "outcome": "completed"})
    ledger.append(_child(SID, gone, home))

    (s,) = _grok_only()

    assert leader["calls"] == 0
    assert s.is_stale  # done and its tree is gone: `mnemo sessions` hides it


def test_one_roster_per_home(ledger, leader, home, tree):
    for sid in (SID, SID2):
        _session_dir(home, tree, sid)
        ledger.append(_child(sid, tree, home))
    leader["rows"] = [_row(SID, home, "working"), _row(SID2, home, "idle")]

    found = _grok_only()

    assert leader["calls"] == 1
    assert {s.short_id: s.live for s in found} == {paths.short_id(SID): True,
                                                    paths.short_id(SID2): True}


def test_scope_keeps_the_repo_and_its_worktrees(ledger, leader, home, tmp_path):
    repo = tmp_path / "repo"
    mine = tmp_path / "repo-wt-c-parser"
    elsewhere = tmp_path / "other"
    for path in (repo, mine, elsewhere):
        path.mkdir()
    ledger.append(_child(SID, mine, home))
    ledger.append(_child(SID2, elsewhere, home))

    found = _grok_only(cwd=str(repo))

    assert [s.session_id for s in found] == [SID]


def test_claude_and_grok_rows_come_back_as_one_sorted_queue(monkeypatch, tmp_path, ledger, leader,
                                                            home, tree):
    jobs_root = tmp_path / "jobs"
    for short in ("00000000", "ffffffff"):
        (jobs_root / short).mkdir(parents=True)
        (jobs_root / short / "state.json").write_text(json.dumps({"state": "working"}),
                                                      encoding="utf-8")
    from mnemo.core.sessions import accounts

    monkeypatch.setattr(accounts, "roots", lambda: [accounts.Root(home=tmp_path, jobs=jobs_root)])
    ledger.append(_child(SID, tree, home))

    found = read_sessions()

    assert [(s.short_id, s.agent) for s in found] == [
        ("00000000", "claude"), (paths.short_id(SID), "grok"), ("ffffffff", "claude")]


# --------------------------------------------------------------------------
# The socket probe


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or sys.platform == "win32",
                    reason="probes a Unix socket")
def test_leader_listening_probes_without_spawning(monkeypatch, tmp_path):
    short = Path(tempfile.mkdtemp(prefix="mg-", dir="/tmp"))
    path = short / "l.sock"
    monkeypatch.setattr(acp, "leader_socket_for", lambda home, *a, **k: str(path))
    try:
        assert grok_sessions.leader_listening(tmp_path) is False  # no file

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        server.listen(1)
        try:
            assert grok_sessions.leader_listening(tmp_path) is True
        finally:
            server.close()
        assert path.exists()
        assert grok_sessions.leader_listening(tmp_path) is False  # left behind, refuses
    finally:
        if path.exists():
            path.unlink()
        short.rmdir()


def test_read_roster_on_a_missing_home_is_empty(tmp_path):
    assert grok_sessions.read_roster(tmp_path / "gone") == {}


# --------------------------------------------------------------------------
# The views


def _emit_json(monkeypatch, found: List[Session], capsys) -> List[dict]:
    from mnemo.cli.commands import sessions as sessions_cmd

    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions",
                        lambda root=None, *, cwd=None, **kw: found)
    monkeypatch.setattr("mnemo.core.sessions.detector.sweep", lambda sessions, *, vault_root: 0)
    args = argparse.Namespace(json=True, watch=False, **{"all": True})
    assert sessions_cmd.cmd_sessions(args) == 0
    return json.loads(capsys.readouterr().out)


def test_json_carries_agent_and_the_same_keys_for_both(monkeypatch, capsys, home, tree):
    directory = _session_dir(home, tree, SID)
    (directory / "signals.json").write_text(json.dumps({"contextTokensUsed": 16378}),
                                            encoding="utf-8")
    grok = Session(short_id=paths.short_id(SID), session_id=SID, state="working", tempo="active",
                   link_scan_path=str(directory / "chat_history.jsonl"), agent="grok")
    claude = Session(short_id="aaaa1111", state="working", tempo="active")

    rows = {r["short_id"]: r for r in _emit_json(monkeypatch, [claude, grok], capsys)}

    assert rows["aaaa1111"]["agent"] == "claude"
    assert rows[grok.short_id]["agent"] == "grok"
    assert set(rows["aaaa1111"]) == set(rows[grok.short_id])
    assert rows[grok.short_id]["context_tokens"] == 16378
    assert rows[grok.short_id]["context_breakdown"] is None


def test_json_names_a_grok_childs_action_in_claude_terms(monkeypatch, capsys, home, tree):
    directory = _session_dir(home, tree, SID)
    _jsonl(directory / "chat_history.jsonl", [
        {"type": "user", "content": [{"type": "text", "text": "<user_query>\ngo\n</user_query>"}],
         "prompt_index": 0},
        {"type": "assistant", "content": "", "model_id": "grok-4.7", "tool_calls": [
            {"id": "c1", "name": "read_file", "arguments": json.dumps({"target_file": "src/a.py"})}]},
    ])
    grok = Session(short_id=paths.short_id(SID), session_id=SID, state="working", tempo="active",
                   link_scan_path=str(directory / "chat_history.jsonl"), agent="grok")

    (row,) = _emit_json(monkeypatch, [grok], capsys)

    assert row["activity"]["tool"] == "Read"
    assert row["activity"]["target"] == "a.py" or "a.py" in row["activity"]["target"]
    assert row["exploration"] is None


def test_grok_context_reads_signals_json(tmp_path):
    from mnemo.core.activity.context import grok_context

    (tmp_path / "signals.json").write_text(json.dumps({"contextTokensUsed": 15263}),
                                           encoding="utf-8")
    assert grok_context(str(tmp_path / "chat_history.jsonl")) == 15263
    assert grok_context(str(tmp_path / "missing" / "chat_history.jsonl")) is None
    assert grok_context(None) is None


def test_the_queue_attaches_a_grok_child_with_grok(home, tree):
    from mnemo.core.sessions.render import render_queue

    directory = _session_dir(home, tree, SID)
    waiting = Session(short_id=paths.short_id(SID), session_id=SID, state="blocked",
                      tempo="blocked", live=True, needs="permission requested: Bash",
                      cwd=str(tree), link_scan_path=str(directory / "chat_history.jsonl"),
                      agent="grok", updated_at="2099-01-01T00:00:00Z")
    out = render_queue([waiting])

    attach = [line for line in out.splitlines() if "attach:" in line]
    assert len(attach) == 1
    assert f"grok --resume {SID}" in attach[0]
    assert "GROK_HOME=" in attach[0]  # not the default home
    assert "claude attach" not in out


def test_the_remove_hint_never_names_a_grok_child():
    from mnemo.core.sessions.render import render_queue

    dead_grok = Session(short_id="3224200f", state="blocked", tempo="blocked", live=False,
                        needs="API error — see detail", agent="grok",
                        updated_at="2026-01-01T00:00:00Z")
    dead_claude = Session(short_id="aaaa1111", state="blocked", tempo="blocked", live=False,
                          needs="q?", updated_at="2026-01-02T00:00:00Z")

    assert "remove: claude rm" not in render_queue([dead_grok])
    assert "  remove: claude rm aaaa1111" in render_queue([dead_grok, dead_claude])


def test_a_claude_queue_renders_as_before():
    from mnemo.core.sessions.render import render_queue

    waiting = Session(short_id="aaaa1111", state="blocked", tempo="blocked", live=True, needs="q?")
    dead = Session(short_id="bbbb2222", state="blocked", tempo="blocked", live=False, needs="q?")
    out = render_queue([waiting, dead])

    assert "  attach: claude attach aaaa1111" in out.splitlines()
    assert "  remove: claude rm bbbb2222" in out.splitlines()


def test_mnemo_session_shows_a_grok_childs_actions(monkeypatch, capsys, home, tree):
    from mnemo.cli.commands import session as session_cmd

    directory = _session_dir(home, tree, SID)
    _jsonl(directory / "chat_history.jsonl", [
        {"type": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "name": "run_terminal_command",
             "arguments": json.dumps({"command": "pytest -q", "description": "Run the suite"})},
            {"id": "c2", "name": "search_replace",
             "arguments": json.dumps({"file_path": "src/b.py", "old_string": "a",
                                      "new_string": "b"})}]},
    ])
    grok = Session(short_id=paths.short_id(SID), session_id=SID, state="working", tempo="active",
                   model="grok-4.7", effort="high", cwd=str(tree),
                   link_scan_path=str(directory / "chat_history.jsonl"), agent="grok")
    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions", lambda root=None, **kw: [grok])

    # the full id finds it too, not only the short one
    assert session_cmd.cmd_session(argparse.Namespace(short_id=SID, limit=15, follow=False)) == 0
    out = capsys.readouterr().out

    assert "Bash" in out and "Edit" in out
    assert "run_terminal_command" not in out
    assert "effort high" in out
    assert f"grok --resume {SID}" in out
    assert "claude attach" not in out
    assert "before first edit" not in out


def test_claude_shaped_events_of_a_missing_file_is_empty(tmp_path):
    assert grok_sessions.claude_shaped_events(str(tmp_path / "nope.jsonl")) == []
    assert grok_sessions.claude_shaped_events(None) == []


def test_sessions_json_asdict_has_agent_for_every_session():
    assert asdict(Session(short_id="a"))["agent"] == "claude"
