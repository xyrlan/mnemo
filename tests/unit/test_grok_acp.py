"""``mnemo.core.grok.acp`` against a fake leader speaking the measured shapes.

No ``grok`` runs here: ``_grok_argv`` is pointed at ``_fake_grok_leader.py``,
which answers with what grok 1.0.60 answered live (the module docstring and
``tests/live/test_grok_leader_live.py`` say how each was measured).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mnemo.core.grok import acp, paths

FAKE = Path(__file__).with_name("_fake_grok_leader.py")
CWD = str(Path("/work/repo"))


@pytest.fixture(autouse=True)
def _state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # HOME patching does nothing on Windows: point mnemo's Grok state at tmp.
    d = tmp_path / "st"
    monkeypatch.setattr(acp, "state_dir", lambda: d)
    return d


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "gh"
    h.mkdir()
    (h / "auth.json").write_text("{}", encoding="utf-8")
    return h


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state_path = tmp_path / "fake-state.json"
    state_path.write_text(json.dumps({"sessions": {}}), encoding="utf-8")
    monkeypatch.setenv("FAKE_GROK_STATE", str(state_path))
    monkeypatch.setattr(acp, "_grok_argv", lambda: [sys.executable, str(FAKE)])

    class Fake:
        path = state_path

        @staticmethod
        def state():
            return json.loads(state_path.read_text(encoding="utf-8"))

        @staticmethod
        def set(**kw):
            s = Fake.state()
            s.update(kw)
            state_path.write_text(json.dumps(s), encoding="utf-8")

        @staticmethod
        def session(sid, **kw):
            s = Fake.state()
            row = {"cwd": CWD, "resident": True, "activity": "idle", "yolo": True}
            row.update(kw)
            s["sessions"][sid] = row
            state_path.write_text(json.dumps(s), encoding="utf-8")

        @staticmethod
        def requests(method):
            return [m for m in Fake.state().get("log", []) if m.get("method") == method]

    return Fake


SID = "01a144cc-77f5-7ea3-9060-06013224200f"


# --- new_session -----------------------------------------------------------

def test_new_session_asks_for_yolo_model_and_effort_and_records_yolo(fake, home):
    sid = acp.Leader(home).new_session(CWD, model="grok-4.7", effort="low")
    (req,) = fake.requests("session/new")
    assert req["params"]["cwd"] == CWD
    assert req["params"]["mcpServers"] == []
    assert req["params"]["_meta"] == {"yoloMode": True, "modelId": "grok-4.7", "reasoningEffort": "low"}
    assert sid in fake.state()["sessions"]
    assert acp.was_created_yolo(home, sid)


def test_new_session_without_yolo_says_nothing_of_it_and_records_nothing(fake, home):
    sid = acp.Leader(home).new_session(CWD, yolo=False)
    (req,) = fake.requests("session/new")
    assert "_meta" not in req["params"]
    assert not acp.was_created_yolo(home, sid)


def test_unknown_model_is_refused_and_the_session_closed(fake, home):
    # Live, grok swaps an unknown modelId for its default without a word.
    with pytest.raises(ValueError, match="no model 'grok-9'.*grok-4.7"):
        acp.Leader(home).new_session(CWD, model="grok-9")
    (close,) = fake.requests("_x.ai/session/close")
    assert fake.state()["sessions"][close["params"]["sessionId"]]["resident"] is False


def test_unknown_effort_is_refused_before_grok_runs(fake, home):
    with pytest.raises(ValueError, match="no effort 'turbo'"):
        acp.Leader(home).new_session(CWD, effort="turbo")
    assert fake.state().get("bridges") is None


# --- prompt ----------------------------------------------------------------

def test_prompt_without_wait_returns_once_the_turn_runs(fake, home):
    fake.session(SID)
    assert acp.Leader(home).prompt(SID, CWD, "go on") is None
    (req,) = fake.requests("session/prompt")
    assert req["params"]["prompt"] == [{"type": "text", "text": "go on"}]
    assert req["params"]["_meta"]["promptId"]
    assert not fake.requests("session/load")


def test_prompt_with_wait_returns_the_last_message_of_its_own_turn(fake, home):
    fake.session(SID)
    fake.set(reply="All done here.")
    text = acp.Leader(home).prompt(SID, CWD, "go on", wait=True)
    # Not the words before the tool call, nor another session's, nor history.
    assert text == "All done here."


def test_prompt_to_a_working_session_returns_once_it_is_queued(fake, home):
    fake.session(SID, activity="working")
    fake.set(busy=True)
    assert acp.Leader(home).prompt(SID, CWD, "after this", timeout=20) is None
    assert fake.state()["sessions"][SID]["prompts"] == ["after this"]


def test_prompt_to_a_dormant_session_loads_it_again_with_yolo_if_mnemo_made_it_so(fake, home):
    leader = acp.Leader(home)
    sid = leader.new_session(CWD)
    assert leader.close(sid) == "closed"
    assert leader.prompt(sid, CWD, "wake up") is None
    (load,) = fake.requests("session/load")
    assert load["params"] == {"sessionId": sid, "cwd": CWD, "mcpServers": [], "_meta": {"yoloMode": True}}
    assert fake.state()["sessions"][sid]["yolo"] is True
    assert fake.state()["sessions"][sid]["prompts"] == ["wake up"]


def test_a_session_mnemo_did_not_make_yolo_is_loaded_in_grok_s_own_mode(fake, home):
    fake.session(SID, resident=False, activity="dormant", yolo=False)
    acp.Leader(home).prompt(SID, CWD, "hello")
    (load,) = fake.requests("session/load")
    assert "_meta" not in load["params"]


def test_prompt_to_a_session_nobody_has_raises_session_not_found(fake, home):
    with pytest.raises(acp.SessionNotFound, match="no Grok session"):
        acp.Leader(home).prompt(SID, CWD, "anyone?")


def test_a_permission_request_is_left_for_a_person_and_other_requests_refused(fake, home):
    fake.session(SID)
    fake.set(ask_permission=True)
    assert acp.Leader(home).prompt(SID, CWD, "go", wait=True) == "DONE"
    replies = {m["id"]: m for m in fake.state()["log"] if "id" in m and "method" not in m}
    assert 900 not in replies
    assert replies[901]["error"]["code"] == -32601


def test_a_rate_limited_turn_raises_rate_limited(fake, home):
    fake.session(SID)
    fake.set(rate_limited=True)
    with pytest.raises(acp.RateLimited):
        acp.Leader(home).prompt(SID, CWD, "go", wait=True)


def test_prompt_times_out_instead_of_hanging(fake, home):
    fake.session(SID, activity="working")
    fake.set(busy=True)
    with pytest.raises(TimeoutError):
        acp.Leader(home).prompt(SID, CWD, "after this", wait=True, timeout=1.5)


# --- roster, close, billing --------------------------------------------------

def test_roster_maps_every_row(fake, home):
    fake.session(SID, activity="working", title="Fix it", effort="high", changed=1792155639264)
    fake.session("01a144d2-9638-7cc1-827f-605550a04547", resident=False, activity="dormant")
    rows = {e.session_id: e for e in acp.Leader(home).roster()}
    live = rows[SID]
    assert live == acp.RosterEntry(session_id=SID, cwd=CWD, activity="working", resident=True,
                                   model_id="grok-4.7", effort="high", title="Fix it",
                                   last_change_ms=1792155639264, home=home)
    dormant = rows["01a144d2-9638-7cc1-827f-605550a04547"]
    assert (dormant.activity, dormant.resident, dormant.effort, dormant.title) == ("dormant", False, None, None)


def test_close_returns_grok_s_outcome(fake, home):
    fake.session(SID)
    leader = acp.Leader(home)
    assert leader.close(SID) == "closed"
    assert leader.close(SID) == "notResident"


def test_billing_is_returned_unchanged(fake, home):
    billing = {"config": {"creditUsagePercent": 63.0, "prepaidBalance": {"val": 0}},
               "subscription_tier": "Free"}
    fake.set(billing=billing)
    assert acp.Leader(home).billing() == billing


def test_an_extension_error_envelope_raises():
    with pytest.raises(acp.AcpError, match="boom"):
        acp._unwrap("_x.ai/sessions/list", {"result": None, "error": {"code": "X", "message": "boom"}})
    assert acp._unwrap("_x.ai/billing", {"config": {}}) == {"config": {}}


# --- GrokUnavailable ---------------------------------------------------------

def test_no_grok_on_path(monkeypatch, home):
    monkeypatch.setattr(acp.shutil, "which", lambda name: None)
    with pytest.raises(acp.GrokUnavailable, match="not on PATH"):
        acp.Leader(home).roster()


def test_a_home_with_no_login_says_how_to_log_in(fake, tmp_path, monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    empty = tmp_path / "logged-out"
    empty.mkdir()
    with pytest.raises(acp.GrokUnavailable, match="no login: run `GROK_HOME=.* grok login`"):
        acp.Leader(empty).roster()
    assert fake.state().get("bridges") is None


def test_a_login_grok_refuses_is_unavailable_not_a_protocol_error(fake, home):
    fake.set(logged_in=False)
    with pytest.raises(acp.GrokUnavailable, match="not logged in"):
        acp.Leader(home).new_session(CWD)


def test_a_leader_that_does_not_come_up_is_unavailable_with_its_words(fake, home):
    fake.set(exit_on_initialize=True)
    with pytest.raises(acp.GrokUnavailable, match="did not come up.*SUN_LEN"):
        acp.Leader(home).roster()


def test_unavailable_is_a_runtime_error_and_covers_protocol_and_missing_sessions():
    assert issubclass(acp.GrokUnavailable, RuntimeError)
    for cls in (acp.AcpError, acp.RateLimited, acp.SessionNotFound):
        assert issubclass(cls, acp.GrokUnavailable)


# --- the leader socket and the bridge's environment ---------------------------

def test_a_home_that_fits_uses_grok_s_own_socket():
    h = Path("/Users/you/.grok-work")
    assert acp.leader_socket_for(h, env={}, platform="darwin") == str(h / "leader.sock")


def test_a_home_too_deep_gets_a_short_socket_stable_per_home(tmp_path):
    deep = tmp_path / ("d" * 120)
    first = acp.leader_socket_for(deep, env={}, platform="darwin")
    assert first != str(deep / "leader.sock")
    assert len(os.fsencode(first)) <= 103
    assert acp.leader_socket_for(deep, env={}, platform="darwin") == first
    other = acp.leader_socket_for(tmp_path / ("e" * 120), env={}, platform="darwin")
    assert other != first


def test_a_symlinked_home_shares_its_target_s_fallback_socket(tmp_path, requires_symlinks):
    real = tmp_path / ("r" * 120)
    real.mkdir()
    link = tmp_path / ("l" * 120)
    link.symlink_to(real, target_is_directory=True)
    assert acp.leader_socket_for(link, env={}, platform="darwin") == \
        acp.leader_socket_for(real, env={}, platform="darwin")


def test_grok_leader_socket_is_honoured_only_for_its_own_home():
    mine, named = Path("/Users/you/.grok"), str(Path("/tmp/s.sock"))
    env = {"GROK_HOME": str(mine), "GROK_LEADER_SOCKET": named}
    assert acp.leader_socket_for(mine, env=env) == named
    other = Path("/Users/you/.grok-work")
    assert acp.leader_socket_for(other, env=env) == str(other / "leader.sock")


def test_socket_limit_per_platform():
    assert acp.socket_limit("darwin") == 103
    assert acp.socket_limit("linux") == 107


def test_the_bridge_gets_the_home_and_no_session_identity(fake, home, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc")
    monkeypatch.setenv("GROK_SESSION_ID", "01a1-parent")
    monkeypatch.delenv("GROK_LEADER_SOCKET", raising=False)
    fake.set(env_keys=["GROK_HOME", "CLAUDE_CODE_SESSION_ID", "GROK_SESSION_ID"])
    acp.Leader(home).roster()
    (env,) = fake.state()["bridges"]
    assert Path(env["GROK_HOME"]) == home
    assert env["CLAUDE_CODE_SESSION_ID"] is None
    assert env["GROK_SESSION_ID"] is None


def test_a_deep_home_s_bridge_is_told_the_short_socket(fake, tmp_path, monkeypatch):
    monkeypatch.delenv("GROK_LEADER_SOCKET", raising=False)
    deep = tmp_path / ("d" * 120)
    deep.mkdir()
    (deep / "auth.json").write_text("{}", encoding="utf-8")
    fake.set(env_keys=["GROK_LEADER_SOCKET"])
    leader = acp.Leader(deep)
    leader.roster()
    (env,) = fake.state()["bridges"]
    assert env["GROK_LEADER_SOCKET"] == leader.socket
    assert Path(leader.socket).parent.is_dir()


def test_leader_defaults_to_the_environment_s_home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "acct"))
    assert acp.Leader().home == tmp_path / "acct"
    assert acp.Leader(tmp_path / "x").home == tmp_path / "x"


# --- finding a session on disk ------------------------------------------------

def _disk_session(home: Path, cwd: str, sid: str) -> Path:
    d = home / "sessions" / paths.encode_cwd(cwd) / sid
    d.mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps({"info": {"id": sid, "cwd": cwd}}), encoding="utf-8")
    return d


def test_find_session_by_full_or_short_id(home):
    d = _disk_session(home, CWD, SID)
    assert acp.find_session(SID, home) == d
    assert acp.find_session(paths.short_id(SID), home) == d
    assert acp.find_session("deadbeef", home) is None
    assert acp.session_cwd(d) == CWD


def test_a_short_id_two_sessions_share_finds_neither(home):
    _disk_session(home, CWD, SID)
    _disk_session(home, str(Path("/work/other")), "01a144dd-0000-7000-8000-06013224200f")
    assert acp.find_session(paths.short_id(SID), home) is None


# --- Claude stays what it was (decision 8) -------------------------------------

def test_loading_every_command_does_not_load_the_grok_client():
    code = ("import sys, mnemo.cli, mnemo.cli.commands; "
            "sys.exit(1 if 'mnemo.core.grok.acp' in sys.modules else 0)")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"))
    assert subprocess.run([sys.executable, "-c", code], env=env).returncode == 0
