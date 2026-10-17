"""A parent learns its Grok child finished, and a Grok parent learns any child
finished (spec 2026-10-16-grok-host, wave 2, grok-notices).

The session files below are shaped like the ones grok 1.0.60 wrote on
2026-10-16 (``~/.grok/sessions/<cwd>/<id>/``): ``events.jsonl`` with
``turn_started``/``turn_ended{outcome}`` stamped ``…Z`` to the millisecond,
``chat_history.jsonl`` records typed ``system``/``user``/``assistant``/
``tool_result``, and ``summary.json`` with ``info.cwd``. The leader is never
started: :class:`FakeLeader` stands in for ``acp.Leader``.
"""
from __future__ import annotations

import io
import json
import os
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from mnemo.core.grok import acp
from mnemo.core.grok import paths as grok_paths
from mnemo.core.sessions import child_notices as cn
from mnemo.core.sessions import held_notices, inbox, parents
from mnemo.core.sessions import report_card as rc
from mnemo.hooks import session_end


@pytest.fixture
def tmp_path(short_tmp_path: Path) -> Path:
    """Short on Windows: these tests nest a percent-encoded cwd under it (#729)."""
    return short_tmp_path

CHILD = "01a15e00-1111-7222-8333-0000c41d0001"
CHILD_SHORT = grok_paths.short_id(CHILD)  # "c41d0001": the end, not the start
PARENT = "01a15e00-1111-7222-8333-00000fa7e500"
CLAUDE_PARENT = "0ff9d810-e54d-41f3-a045-b0ccff6c5186"
T0 = 1_790_000_000.0
ON = {"dispatch": {"notifyParent": True}}
PR_URL = "https://github.com/o/r/pull/41"
REPORT = "Closing report: the notices reach a Grok parent.\nPR #41 is open."


@pytest.fixture(autouse=True)
def _isolated(tmp_tempdir, monkeypatch):
    for name in ("GROK_HOOK_EVENT", "GROK_SESSION_ID", "GROK_HOME", "GROK_LEADER_SOCKET",
                 session_end.GROK_PAYLOAD_ENV):
        monkeypatch.delenv(name, raising=False)
    return tmp_tempdir


@dataclass
class Child:
    """A ``ChildAgent`` line as grok-spawn writes it."""

    short_id: str
    agent: str
    session_id: str
    home: Optional[str]
    cwd: str


def _child_agents(monkeypatch, children: List[Child]) -> None:
    """Stand in for grok-spawn's ``child_agents`` ledger."""
    import mnemo.core.sessions as package

    module = types.ModuleType("mnemo.core.sessions.child_agents")
    module.read = lambda vault_root: {c.short_id: c for c in children}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(package, "child_agents", module, raising=False)


def _iso(epoch: float) -> str:
    import time

    ms = int(round((epoch % 1) * 1000))
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch)) + f".{ms:03d}Z"


def _session(home: Path, cwd: str, sid: str, *, turns=(), history=None) -> Path:
    """A Grok session directory. *turns*: ``(started_at, ended_at, outcome)``,
    ``ended_at`` ``None`` for a turn still running."""
    folder = home / "sessions" / grok_paths.encode_cwd(cwd) / sid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "summary.json").write_text(json.dumps({"info": {"id": sid, "cwd": cwd}}),
                                         encoding="utf-8")
    lines = []
    for n, (started, ended, outcome) in enumerate(turns):
        lines.append({"ts": _iso(started), "type": "turn_started", "session_id": sid,
                      "turn_number": n, "model_id": "grok-4.7"})
        lines.append({"ts": _iso(started + 0.1), "type": "phase_changed",
                      "phase": "waiting_for_model"})
        if ended is not None:
            lines.append({"ts": _iso(ended), "type": "turn_ended", "outcome": outcome})
    (folder / cn.GROK_EVENTS).write_text("".join(json.dumps(r) + "\n" for r in lines),
                                         encoding="utf-8")
    records = history if history is not None else _history()
    (folder / grok_paths.CHAT_HISTORY).write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    (folder / grok_paths.UPDATES).write_text("{}\n", encoding="utf-8")
    return folder


def _history(report: str = REPORT) -> List[dict]:
    """A child's history: its prompt, a ``gh pr create``, then its report."""
    call = {"id": "call_1", "name": "run_terminal_command",
            "arguments": json.dumps({"command": "gh pr create --fill"})}
    return [
        {"type": "system", "content": "You are Grok 4.7"},
        {"type": "user", "content": [{"type": "text", "text": "<user_info>\nOS: macos\n</user_info>"}]},
        {"type": "user", "content": [{"type": "text", "text": "<user_query>\nBuild the piece.\n</user_query>"}]},
        {"type": "assistant", "content": "Opening the PR.", "tool_calls": [call], "model_id": "grok-4.7"},
        {"type": "tool_result", "tool_call_id": "call_1", "content": PR_URL + "\n"},
        {"type": "reasoning"},
        {"type": "assistant", "content": report, "model_id": "grok-4.7"},
    ]


def _link(vault: Path, short_id: str = CHILD_SHORT, parent: str = PARENT) -> None:
    path = parents.log_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"short_id": short_id, "parent_session": parent}) + "\n")


def _config(vault: Path, monkeypatch, **cfg) -> dict:
    full = {"vaultRoot": str(vault), "dispatch": {"notifyParent": True}}
    full.update(cfg)
    path = vault / "mnemo.config.json"
    path.write_text(json.dumps(full), encoding="utf-8")
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(path))
    return full


class FakeLeader:
    """``acp.Leader`` with no leader: records what it is asked."""

    prompts: List[dict] = []
    resident: List[str] = []
    fail: Optional[Exception] = None

    def __init__(self, home=None):
        self.home = Path(home) if home is not None else grok_paths.grok_home()

    def prompt(self, session_id, cwd, text, *, wait=False, timeout=600):
        if FakeLeader.fail is not None:
            raise FakeLeader.fail
        FakeLeader.prompts.append({"home": self.home, "session_id": session_id, "cwd": cwd,
                                   "text": text, "wait": wait})
        return None

    def roster(self):
        return [acp.RosterEntry(session_id=sid, cwd="", activity="idle", resident=True,
                                model_id=None, effort=None, title=None, last_change_ms=None,
                                home=self.home) for sid in FakeLeader.resident]


@pytest.fixture
def leader(monkeypatch):
    FakeLeader.prompts, FakeLeader.resident, FakeLeader.fail = [], [], None
    monkeypatch.setattr(acp, "Leader", FakeLeader)
    return FakeLeader


# --- a Grok session's last turn -------------------------------------------------


def test_the_last_turn_event_names_the_stop(tmp_path):
    folder = _session(tmp_path / "home", "/w/app", CHILD,
                      turns=[(T0, T0 + 20, "completed"), (T0 + 60, T0 + 90, "completed")])
    with (folder / cn.GROK_EVENTS).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": _iso(T0 + 91), "type": "phase_changed", "phase": "idle"}) + "\n")
    turn = cn.grok_turn(folder, chunk=64)  # across chunk boundaries
    assert turn is not None and turn.finished
    assert turn.stamp == _iso(T0 + 90) and turn.at == pytest.approx(T0 + 90, abs=0.01)


def test_a_running_turn_and_a_failed_one_are_not_finishes(tmp_path):
    running = cn.grok_turn(_session(tmp_path / "a", "/w/app", CHILD, turns=[(T0, None, "")]))
    assert running is not None and not running.ended and not running.finished
    failed = cn.grok_turn(_session(tmp_path / "b", "/w/app", CHILD, turns=[(T0, T0 + 5, "error")]))
    assert failed is not None and failed.ended and not failed.finished
    assert cn.grok_turn(tmp_path / "nowhere") is None


# --- what is due ---------------------------------------------------------------


def _child(home: Path, cwd: str = "/w/app-wt-c-grok") -> Child:
    return Child(short_id=CHILD_SHORT, agent="grok", session_id=CHILD, home=str(home), cwd=cwd)


def _scan_grok(vault: Path, child: Child, *, now: float, armed_at: float = 0.0, **kw):
    return cn.scan_grok([child], links=parents.read(vault), rows=cn.report_rows(vault),
                        now=now, armed_at=armed_at, **kw)


def test_a_finished_grok_child_is_due_once_the_grace_is_over(tmp_vault, tmp_path):
    home = tmp_path / "grok"
    child = _child(home)
    folder = _session(home, child.cwd, CHILD, turns=[(T0, T0 + 30, "completed")])
    _link(tmp_vault)

    early = _scan_grok(tmp_vault, child, now=T0 + 30 + cn.GROK_GRACE_SECONDS - 1)
    assert early.due == [] and early.waiting == 1

    late = _scan_grok(tmp_vault, child, now=T0 + 30 + cn.GROK_GRACE_SECONDS + 1)
    (due,) = late.due
    assert (due.short_id, due.session_id, due.parent, due.agent) == (CHILD_SHORT, CHILD, PARENT, "grok")
    assert due.stamp == _iso(T0 + 30)
    assert due.transcript == folder / grok_paths.CHAT_HISTORY


def test_a_grok_child_mid_turn_keeps_the_watcher_and_is_not_due(tmp_vault, tmp_path):
    home = tmp_path / "grok"
    child = _child(home)
    _session(home, child.cwd, CHILD, turns=[(T0, T0 + 30, "completed"), (T0 + 400, None, "")])
    _link(tmp_vault)
    found = _scan_grok(tmp_vault, child, now=T0 + 1000)
    assert found.due == [] and found.running == 1


def test_what_is_never_a_grok_finish(tmp_vault, tmp_path):
    home = tmp_path / "grok"
    child = _child(home)
    _link(tmp_vault)
    # A turn cut off by an error is a stall, the resume path's to wake.
    _session(home, child.cwd, CHILD, turns=[(T0, T0 + 30, "error")])
    assert _scan_grok(tmp_vault, child, now=T0 + 1000).due == []
    # Before arming, or past the lookback.
    _session(home, child.cwd, CHILD, turns=[(T0, T0 + 30, "completed")])
    assert _scan_grok(tmp_vault, child, now=T0 + 1000, armed_at=T0 + 31).due == []
    assert _scan_grok(tmp_vault, child, now=T0 + 31 + cn.LOOKBACK_SECONDS).due == []
    # Already finished: its pipeline ran.
    assert _scan_grok(tmp_vault, child, now=T0 + 1000,
                      finished={(CHILD_SHORT, _iso(T0 + 30))}).due == []
    # A Claude child's line, or a child nobody dispatched.
    claude = Child(CHILD_SHORT, "claude", CHILD, str(home), child.cwd)
    assert _scan_grok(tmp_vault, claude, now=T0 + 1000).due == []
    orphan = Child("deadbeef", "grok", CHILD, str(home), child.cwd)
    assert _scan_grok(tmp_vault, orphan, now=T0 + 1000).due == []
    # Files gone.
    gone = _child(tmp_path / "no-such-home")
    assert _scan_grok(tmp_vault, gone, now=T0 + 1000).due == []


def test_the_claude_scan_leaves_grok_sessions_to_the_grok_scan(tmp_vault):
    @dataclass
    class S:
        short_id: str = CHILD_SHORT
        session_id: str = CHILD
        state: str = "stopped"
        updated_at: str = _iso(T0)
        cwd: str = "/w/app"
        live: bool = False
        parent_session: Optional[str] = None
        agent: str = "grok"

    _link(tmp_vault)
    found = cn.scan([S()], links=parents.read(tmp_vault), rows={}, now=T0 + 1000,
                    armed_at=0.0, find=lambda _s: None, turn_of=lambda _t: None)
    assert found.due == [] and found.running == 0


# --- once ----------------------------------------------------------------------


def test_a_grok_stop_is_claimed_once(tmp_vault):
    assert cn.claim_finish(tmp_vault, CHILD_SHORT, _iso(T0), via=cn.GROK_VIA)
    assert not cn.claim_finish(tmp_vault, CHILD_SHORT, _iso(T0), via="session_end")
    assert cn.claim_finish(tmp_vault, CHILD_SHORT, _iso(T0 + 600))  # a later stop
    assert cn.finished_stops(tmp_vault) == {(CHILD_SHORT, _iso(T0)), (CHILD_SHORT, _iso(T0 + 600))}
    rows = [json.loads(line) for line in
            (tmp_vault / ".mnemo" / cn.FINISHES_NAME).read_text(encoding="utf-8").splitlines()]
    assert rows[0]["via"] == cn.GROK_VIA


def test_a_busy_ledger_claims_nothing(tmp_vault):
    lock = tmp_vault / ".mnemo" / cn.FINISHES_LOCK_NAME
    lock.mkdir(parents=True)
    assert not cn.claim_finish(tmp_vault, CHILD_SHORT, _iso(T0), wait=0.1)
    assert cn.finished_stops(tmp_vault) == set()


# --- the watcher hands the finish to the hook's own worker ----------------------


def test_the_sweep_hands_a_grok_finish_to_the_session_end_worker(tmp_vault, tmp_path, monkeypatch):
    home = tmp_path / "grok"
    child = _child(home)
    folder = _session(home, child.cwd, CHILD, turns=[(T0, T0 + 30, "completed")])
    _link(tmp_vault)
    _child_agents(monkeypatch, [child])
    cn.armed_at(tmp_vault, now=0)
    handed = []
    monkeypatch.setattr(session_end, "_write_grok_payload", handed.append)

    report = cn.sweep(ON, vault_root=tmp_vault, now=T0 + 500, reader=lambda: [])
    assert report.told == [CHILD_SHORT]
    (payload,) = handed
    assert payload["session_id"] == CHILD and payload["cwd"] == child.cwd
    assert payload["reason"] == session_end.GROK_FINISHED_REASON
    assert payload[session_end.GROK_FINISH_KEY] == _iso(T0 + 30)
    assert payload[session_end.GROK_VIA_KEY] == cn.GROK_VIA
    assert Path(payload["transcript_path"]) == folder / grok_paths.UPDATES

    # A second tick, or a restarted watcher, hands nothing more over.
    again = cn.sweep(ON, vault_root=tmp_vault, now=T0 + 530, reader=lambda: [])
    assert again.told == [] and len(handed) == 1


def test_a_running_grok_child_starts_a_watcher_at_session_start(tmp_vault, tmp_path, monkeypatch):
    home = tmp_path / "grok"
    child = _child(home)
    _session(home, child.cwd, CHILD, turns=[(T0, None, "")])
    _link(tmp_vault)
    spawned = []
    monkeypatch.setattr(cn.time, "time", lambda: T0 + 60)
    assert cn.on_session_start(ON, vault_root=tmp_vault, spawn=lambda: spawned.append(1),
                               reader=lambda: [], children=lambda: [child]) == "spawned"
    assert spawned == [1]
    assert cn.on_session_start(ON, vault_root=tmp_vault, spawn=lambda: spawned.append(1),
                               reader=lambda: [], children=lambda: []) == "nothing"


def _work(payload: dict, tmp_path: Path, monkeypatch) -> None:
    """Run the session-end worker over *payload*, as the hand-off starts it."""
    handoff = tmp_path / f"handoff-{len(list(tmp_path.glob('handoff-*')))}.json"
    handoff.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv(session_end.GROK_PAYLOAD_ENV, str(handoff))
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert session_end.main() == 0


def _spy_end(monkeypatch) -> Dict[str, list]:
    seen: Dict[str, list] = {"notify": [], "follow": [], "extract": [], "brief": []}
    monkeypatch.setattr(session_end, "_maybe_notify_parent",
                        lambda cfg, vault, **kw: seen["notify"].append(kw))
    monkeypatch.setattr(session_end, "_maybe_follow_pr",
                        lambda cfg, vault, **kw: seen["follow"].append(kw))
    monkeypatch.setattr(session_end, "_maybe_schedule_extraction",
                        lambda *a, **kw: seen["extract"].append(a))
    monkeypatch.setattr(session_end, "_maybe_schedule_briefing",
                        lambda *a, **kw: seen["brief"].append(kw))
    monkeypatch.setattr(session_end, "_maybe_sweep_sessions", lambda vault: None)
    return seen


def test_the_pipeline_runs_once_whichever_end_comes_first(tmp_vault, tmp_path, monkeypatch):
    home = tmp_path / "grok"
    child = _child(home, cwd=str(tmp_path / "wt"))
    folder = _session(home, child.cwd, CHILD, turns=[(T0, T0 + 30, "completed")])
    _config(tmp_vault, monkeypatch)
    seen = _spy_end(monkeypatch)
    handed = []
    monkeypatch.setattr(session_end, "_write_grok_payload", handed.append)
    session_end.hand_off_grok_finish(session_id=CHILD, cwd=child.cwd,
                                     history=folder / grok_paths.CHAT_HISTORY,
                                     stamp=_iso(T0 + 30), via=cn.GROK_VIA)
    (payload,) = handed

    _work(payload, tmp_path, monkeypatch)
    assert [k["short"] for k in seen["notify"]] == [CHILD_SHORT]
    assert seen["notify"][0]["via"] == cn.GROK_VIA
    assert Path(seen["notify"][0]["transcript"]) == folder / grok_paths.CHAT_HISTORY
    assert len(seen["follow"]) == len(seen["extract"]) == len(seen["brief"]) == 1

    # The same handed-off finish again (a second watcher), then the session's
    # own SessionEnd when it is closed later: both stand down.
    _work(payload, tmp_path, monkeypatch)
    closed = {"hook_event_name": "SessionEnd", "session_id": CHILD, "cwd": child.cwd,
              "reason": "channel_closed",
              "transcript_path": str(folder / grok_paths.UPDATES)}
    _work(closed, tmp_path, monkeypatch)
    assert len(seen["notify"]) == len(seen["follow"]) == len(seen["extract"]) == 1
    assert len(seen["brief"]) == 1

    # Woken again (pr-follow) and finished again: a new stop, told again.
    _session(home, child.cwd, CHILD, turns=[(T0, T0 + 30, "completed"),
                                            (T0 + 900, T0 + 960, "completed")])
    _work(closed, tmp_path, monkeypatch)
    assert len(seen["notify"]) == 2 and seen["notify"][1]["via"] == ""


def test_a_session_end_that_comes_first_leaves_the_watcher_nothing(tmp_vault, tmp_path, monkeypatch):
    home = tmp_path / "grok"
    child = _child(home, cwd=str(tmp_path / "wt"))
    folder = _session(home, child.cwd, CHILD, turns=[(T0, T0 + 30, "completed")])
    _config(tmp_vault, monkeypatch)
    seen = _spy_end(monkeypatch)
    _work({"hook_event_name": "SessionEnd", "session_id": CHILD, "cwd": child.cwd,
           "reason": "shutdown", "transcript_path": str(folder / grok_paths.UPDATES)},
          tmp_path, monkeypatch)
    assert len(seen["notify"]) == 1

    _link(tmp_vault)
    found = _scan_grok(tmp_vault, child, now=T0 + 1000,
                       finished=cn.finished_stops(tmp_vault))
    assert found.due == []


def test_a_claude_session_end_never_touches_the_grok_ledger(tmp_vault, tmp_path, monkeypatch):
    _config(tmp_vault, monkeypatch)
    seen = _spy_end(monkeypatch)
    payload = {"session_id": "594436f2-a282-4a1c-b04a-08441296047e", "cwd": str(tmp_path),
               "hook_event_name": "SessionEnd", "reason": "exit"}
    for _ in range(2):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        assert session_end.main() == 0
    assert len(seen["notify"]) == 2 and seen["notify"][0].get("short", "") == ""
    assert not (tmp_vault / ".mnemo" / cn.FINISHES_NAME).exists()


# --- the report card, from chat_history.jsonl -----------------------------------


def test_the_card_reads_the_report_and_the_pr_from_a_grok_history(tmp_path):
    folder = _session(tmp_path / "grok", "/w/app", CHILD, turns=[(T0, T0 + 30, "completed")])
    history = folder / grok_paths.CHAT_HISTORY
    assert rc.closing_report(history) == REPORT
    assert rc.pr_urls_in(history) == [PR_URL]

    def run(argv, cwd):
        if argv[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps({"number": 41, "url": PR_URL, "state": "OPEN",
                                  "isDraft": False, "mergeable": "MERGEABLE"}), ""
        if argv[:3] == ["gh", "pr", "checks"]:
            return 0, json.dumps([{"name": "test", "bucket": "pass"}]), ""
        return 1, "", "no"

    card = rc.gather(CHILD_SHORT, cwd=None, transcript=history, run=run)
    text = rc.render(card)
    assert card.pr is not None and card.pr.number == 41 and rc.state(card) == "ready"
    assert text.startswith(f'{inbox.NOTICE_PREFIX} id="{CHILD_SHORT}" state="ready">')
    assert "  > Closing report: the notices reach a Grok parent." in text


def test_a_claude_transcript_is_read_as_before(tmp_path):
    path = tmp_path / "594436f2.jsonl"
    path.write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "the report"}]}}) + "\n" + json.dumps({
            "type": "user", "message": {"content": [
                {"type": "tool_result", "content": PR_URL}]}}) + "\n", encoding="utf-8")
    assert rc.closing_report(path) == "the report"
    assert rc.pr_urls_in(path) == [PR_URL]


# --- a Grok parent --------------------------------------------------------------


def test_a_grok_parent_is_found_by_its_session_on_disk(tmp_vault, tmp_path, monkeypatch):
    home = tmp_path / "grok"
    _session(home, "/w/parent", PARENT)
    monkeypatch.setenv("GROK_HOME", str(home))
    address, why = inbox.resolve(tmp_vault, PARENT)
    assert why == ""
    assert address == {"session_id": PARENT, "agent": inbox.GROK, "home": str(home),
                       "cwd": "/w/parent"}


def test_a_parent_found_nowhere_reads_as_it_always_did(tmp_vault):
    assert inbox.resolve(tmp_vault, CLAUDE_PARENT) == (
        None, f"no address recorded for the parent in {inbox.LOG_NAME}")


def test_the_home_dispatch_recorded_finds_a_parent_no_default_home_holds(
        tmp_vault, tmp_path, monkeypatch):
    elsewhere = tmp_path / "accounts" / "work"  # not ~/.grok, not $GROK_HOME
    _session(elsewhere, "/w/parent", PARENT)
    assert inbox.grok_address(tmp_vault, PARENT) is None
    _link(tmp_vault)
    _child_agents(monkeypatch, [_child(elsewhere)])
    assert inbox.grok_address(tmp_vault, PARENT)["home"] == str(elsewhere)


def test_a_notice_reaches_a_grok_parent_as_a_turn_through_its_leader(
        tmp_vault, tmp_path, monkeypatch, leader):
    home = tmp_path / "grok"
    _session(home, "/w/parent", PARENT)
    monkeypatch.setenv("GROK_HOME", str(home))
    assert inbox.deliver(tmp_vault, PARENT, "<mnemo-child-finished id=\"x\">") == ""
    assert inbox.notify(tmp_vault, PARENT, "second")
    assert [(p["home"], p["session_id"], p["cwd"], p["wait"]) for p in leader.prompts] == [
        (home, PARENT, "/w/parent", False)] * 2


def test_a_leader_that_cannot_take_the_turn_says_why(tmp_vault, tmp_path, monkeypatch, leader):
    home = tmp_path / "grok"
    _session(home, "/w/parent", PARENT)
    monkeypatch.setenv("GROK_HOME", str(home))
    leader.fail = acp.GrokUnavailable("Grok home has no login")
    why = inbox.deliver(tmp_vault, PARENT, "x")
    assert why.startswith("Grok leader of ") and "no login" in why
    assert not inbox.notify(tmp_vault, PARENT, "x")


def test_a_parent_open_in_a_tui_outside_the_leader_is_never_loaded(
        tmp_vault, tmp_path, monkeypatch, leader):
    home = tmp_path / "grok"
    _session(home, "/w/parent", PARENT)
    monkeypatch.setenv("GROK_HOME", str(home))
    (home / inbox.ACTIVE_SESSIONS).write_text(json.dumps([
        {"session_id": PARENT, "pid": os.getpid(), "cwd": "/w/parent",
         "opened_at": "2026-10-16T13:00:00Z"}]), encoding="utf-8")
    why = inbox.deliver(tmp_vault, PARENT, "x")
    assert "open in a TUI outside the leader" in why and leader.prompts == []
    # The same TUI in leader mode: the leader has it, so the turn is safe.
    leader.resident = [PARENT]
    assert inbox.deliver(tmp_vault, PARENT, "x") == "" and len(leader.prompts) == 1


def test_a_crashed_tuis_row_does_not_hold_the_notice(tmp_vault, tmp_path, monkeypatch, leader):
    home = tmp_path / "grok"
    _session(home, "/w/parent", PARENT)
    monkeypatch.setenv("GROK_HOME", str(home))
    (home / inbox.ACTIVE_SESSIONS).write_text(json.dumps([
        {"session_id": PARENT, "pid": 0, "cwd": "/w/parent"}]), encoding="utf-8")
    assert inbox.deliver(tmp_vault, PARENT, "x") == ""


# --- end to end: a finished Grok child tells its Grok parent --------------------


def test_a_grok_parent_gets_its_grok_childs_card(tmp_vault, tmp_path, monkeypatch, leader):
    from mnemo.cli.runtime import main as cli_main

    home = tmp_path / "grok"
    monkeypatch.setenv("GROK_HOME", str(home))
    child = _child(home, cwd=str(tmp_path / "wt"))
    folder = _session(home, child.cwd, CHILD, turns=[(T0, T0 + 30, "completed")])
    _session(home, "/w/parent", PARENT)
    _link(tmp_vault)
    _config(tmp_vault, monkeypatch)
    reporters = []
    monkeypatch.setattr(session_end, "_spawn_detached_child_report",
                        lambda short_id, **kw: reporters.append((short_id, kw)) or 4242)
    monkeypatch.setattr(session_end, "_maybe_follow_pr", lambda *a, **k: None)
    monkeypatch.setattr(session_end, "_maybe_sweep_sessions", lambda vault: None)

    _work({"hook_event_name": "SessionEnd", "session_id": CHILD, "cwd": child.cwd,
           "reason": session_end.GROK_FINISHED_REASON, "transcript_path": str(folder / "updates.jsonl"),
           session_end.GROK_FINISH_KEY: _iso(T0 + 30), session_end.GROK_VIA_KEY: cn.GROK_VIA},
          tmp_path, monkeypatch)
    (short_id, kw), = reporters
    assert short_id == CHILD_SHORT and kw["parent"] == PARENT
    assert Path(kw["transcript"]) == folder / grok_paths.CHAT_HISTORY

    # The reporter it started, with gh answered here.
    real = rc.gather

    def run(argv, cwd):
        if argv[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps({"number": 41, "url": PR_URL, "state": "MERGED"}), ""
        return 1, "", "no"

    monkeypatch.setattr(rc, "gather", lambda sid, **k: real(sid, run=run, **k))
    assert cli_main(["child-report", short_id, "--parent", kw["parent"],
                     "--transcript", kw["transcript"]]) == 0
    (sent,) = leader.prompts
    assert sent["session_id"] == PARENT
    assert sent["text"].startswith(f'{inbox.NOTICE_PREFIX} id="{CHILD_SHORT}" state="merged">')
    assert "  > Closing report: the notices reach a Grok parent." in sent["text"]


def test_an_undelivered_card_is_held_for_the_grok_parents_next_turn(
        tmp_vault, tmp_path, monkeypatch, leader):
    from mnemo.cli.runtime import main as cli_main

    home = tmp_path / "grok"
    monkeypatch.setenv("GROK_HOME", str(home))
    folder = _session(home, str(tmp_path / "wt"), CHILD, turns=[(T0, T0 + 30, "completed")])
    _session(home, "/w/parent", PARENT)
    _config(tmp_vault, monkeypatch)
    leader.fail = acp.GrokUnavailable("leader did not come up")
    real = rc.gather
    monkeypatch.setattr(rc, "gather",
                        lambda sid, **k: real(sid, run=lambda a, c: (1, "", "no"), **k))
    assert cli_main(["child-report", CHILD_SHORT, "--parent", PARENT, "--transcript",
                     str(folder / grok_paths.CHAT_HISTORY)]) == 0
    (held,) = held_notices.claim(tmp_vault, PARENT)
    assert held["short_id"] == CHILD_SHORT
    assert "Closing report: the notices reach a Grok parent." in str(held["text"])
