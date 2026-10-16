"""``mnemo grok``: the verbs mnemo-desktop drives Grok sessions with.

The leader is the fake in ``_fake_grok_leader.py``; the JSON shapes are the
ones ``design/contracts/2026-10-16-grok-host-wave1.md`` gives the desktop.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mnemo import cli
from mnemo.core.grok import acp, paths
from mnemo.core.sessions import grok_sessions

FAKE = Path(__file__).with_name("_fake_grok_leader.py")
SID = "01a144cc-77f5-7ea3-9060-06013224200f"
CWD = str(Path("/work/repo"))


@pytest.fixture(autouse=True)
def _state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acp, "state_dir", lambda: tmp_path / "st")


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state_path = tmp_path / "fake-state.json"
    state_path.write_text(json.dumps({"sessions": {}}), encoding="utf-8")
    monkeypatch.setenv("FAKE_GROK_STATE", str(state_path))
    monkeypatch.setattr(acp, "_grok_argv", lambda: [sys.executable, str(FAKE)])
    # The fake bridges stdio and binds no socket: say its leader is listening.
    monkeypatch.setattr(grok_sessions, "leader_listening", lambda home: True)

    class Fake:
        @staticmethod
        def state():
            return json.loads(state_path.read_text(encoding="utf-8"))

        @staticmethod
        def write(state):
            state_path.write_text(json.dumps(state), encoding="utf-8")

        @staticmethod
        def session(sid, **kw):
            s = Fake.state()
            row = {"cwd": CWD, "resident": True, "activity": "idle", "yolo": True}
            row.update(kw)
            s["sessions"][sid] = row
            Fake.write(s)

    return Fake


def _home(root: Path, name: str, *, login: bool = True) -> Path:
    h = root / name
    h.mkdir(parents=True)
    if login:
        (h / "auth.json").write_text("{}", encoding="utf-8")
    return h


def _on_disk(home: Path, sid: str = SID, cwd: str = CWD) -> None:
    d = home / "sessions" / paths.encode_cwd(cwd) / sid
    d.mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps({"info": {"id": sid, "cwd": cwd}}), encoding="utf-8")


def test_roster_json_has_the_desktop_s_shape(fake, tmp_path, capsys):
    home = _home(tmp_path, "gh")
    fake.session(SID, activity="needs_input", title="Fix it", effort="high", changed=1792155639264)
    assert cli.main(["grok", "roster", "--home", str(home), "--json"]) == 0
    (row,) = json.loads(capsys.readouterr().out)
    assert row == {
        "session_id": SID, "short_id": "3224200f", "cwd": CWD, "activity": "needs_input",
        "resident": True, "model_id": "grok-4.7", "effort": "high", "title": "Fix it",
        "last_change_ms": 1792155639264, "home": str(home),
    }


def test_roster_without_home_covers_every_logged_in_home(fake, tmp_path, monkeypatch, capsys):
    a, b = _home(tmp_path, "a"), _home(tmp_path, "b")
    _home(tmp_path, "nobody", login=False)
    monkeypatch.setattr(paths, "grok_homes", lambda env=None: [a, tmp_path / "nobody", b])
    fake.session(SID)
    assert cli.main(["grok", "roster", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    # The fake leader is one for every home; what matters is each home is asked once.
    assert sorted(Path(r["home"]) for r in rows) == sorted([a, b])
    assert len(fake.state()["bridges"]) == 2


def test_roster_with_no_homes_is_an_empty_list(fake, monkeypatch, capsys):
    monkeypatch.setattr(paths, "grok_homes", lambda env=None: [])
    assert cli.main(["grok", "roster", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_roster_of_a_home_whose_leader_is_down_fails_with_the_reason(fake, tmp_path, capsys):
    home = _home(tmp_path, "gh")
    s = fake.state()
    s["exit_on_initialize"] = True
    fake.write(s)
    assert cli.main(["grok", "roster", "--home", str(home), "--json"]) == 1
    assert "did not come up" in capsys.readouterr().err


def _no_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    def spawn():
        raise AssertionError("roster started grok's leader bridge")

    monkeypatch.setattr(acp, "_grok_argv", spawn)
    monkeypatch.setattr(grok_sessions, "leader_listening", lambda home: False)


def test_roster_with_no_leader_listening_reads_the_disk_and_starts_nothing(tmp_path, monkeypatch, capsys):
    _no_bridge(monkeypatch)
    home = _home(tmp_path, "gh")
    old, new = "01a144cc-0000-7ea3-9060-000000000001", SID
    _on_disk(home, old)
    _on_disk(home, new, cwd=str(Path("/work/other")))
    summary = home / "sessions" / paths.encode_cwd(str(Path("/work/other"))) / new / "summary.json"
    summary.write_text(json.dumps({
        "info": {"id": new, "cwd": str(Path("/work/other"))}, "generated_title": "Fix it",
        "current_model_id": "grok-4.7", "reasoning_effort": "high",
        "updated_at": "2026-10-16T12:00:00.123456Z"}), encoding="utf-8")
    headless = home / "sessions" / paths.encode_cwd(CWD) / "01a144cc-0000-7ea3-9060-0000000000ff"
    headless.mkdir()
    (headless / "summary.json").write_text(json.dumps({"session_kind": "headless"}), encoding="utf-8")

    assert cli.main(["grok", "roster", "--home", str(home), "--json"]) == 0
    first, second = json.loads(capsys.readouterr().out)
    assert first == {
        "session_id": new, "short_id": "3224200f", "cwd": str(Path("/work/other")),
        "activity": "dormant", "resident": False, "model_id": "grok-4.7", "effort": "high",
        "title": "Fix it", "last_change_ms": 1792152000123, "home": str(home),
    }
    assert (second["session_id"], second["activity"], second["resident"]) == (old, "dormant", False)
    assert second["cwd"] == CWD and second["last_change_ms"] is None


def test_roster_of_a_home_with_no_leader_and_no_sessions_is_empty(tmp_path, monkeypatch, capsys):
    _no_bridge(monkeypatch)
    home = _home(tmp_path, "gh")
    assert cli.main(["grok", "roster", "--home", str(home), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_roster_of_a_missing_home_says_so(tmp_path, monkeypatch, capsys):
    _no_bridge(monkeypatch)
    assert cli.main(["grok", "roster", "--home", str(tmp_path / "nope"), "--json"]) == 1
    assert "does not exist" in capsys.readouterr().err


def test_roster_text(fake, tmp_path, capsys):
    home = _home(tmp_path, "gh")
    fake.session(SID, activity="working", title="Fix it")
    assert cli.main(["grok", "roster", "--home", str(home)]) == 0
    assert capsys.readouterr().out.split() == ["3224200f", "working", CWD, "Fix", "it"]


def test_send_starts_a_turn_and_exits_zero(fake, tmp_path):
    home = _home(tmp_path, "gh")
    _on_disk(home)
    fake.session(SID)
    assert cli.main(["grok", "send", SID, "go on", "--home", str(home)]) == 0
    assert fake.state()["sessions"][SID]["prompts"] == ["go on"]


def test_send_takes_a_short_id_and_loads_a_dormant_session_in_its_cwd(fake, tmp_path):
    home = _home(tmp_path, "gh")
    _on_disk(home)
    fake.session(SID, resident=False, activity="dormant")
    assert cli.main(["grok", "send", "3224200f", "wake", "up", "--home", str(home)]) == 0
    (load,) = [m for m in fake.state()["log"] if m.get("method") == "session/load"]
    assert load["params"]["cwd"] == CWD
    assert fake.state()["sessions"][SID]["prompts"] == ["wake up"]


def test_send_to_a_session_that_is_not_there_exits_one_with_the_reason(fake, tmp_path, capsys):
    home = _home(tmp_path, "gh")
    assert cli.main(["grok", "send", SID, "hello", "--home", str(home)]) == 1
    assert "no Grok session" in capsys.readouterr().err
    assert fake.state().get("bridges") is None


def test_send_when_the_leader_does_not_know_it_exits_one(fake, tmp_path, capsys):
    home = _home(tmp_path, "gh")
    _on_disk(home)  # on disk, but the leader cannot load it
    assert cli.main(["grok", "send", SID, "hello", "--home", str(home)]) == 1
    assert "no Grok session" in capsys.readouterr().err


def test_send_without_text_is_a_usage_error(fake, tmp_path, capsys):
    assert cli.main(["grok", "send", SID, "--home", str(_home(tmp_path, "gh"))]) == 2
    assert "needs a session id and the text" in capsys.readouterr().err


def test_close_prints_grok_s_outcome(fake, tmp_path, capsys):
    home = _home(tmp_path, "gh")
    _on_disk(home)
    fake.session(SID)
    assert cli.main(["grok", "close", SID, "--home", str(home)]) == 0
    assert cli.main(["grok", "close", SID, "--home", str(home)]) == 0
    assert capsys.readouterr().out.split() == ["closed", "notResident"]


def test_close_of_an_unknown_session_exits_one(fake, tmp_path, capsys):
    assert cli.main(["grok", "close", SID, "--home", str(_home(tmp_path, "gh"))]) == 1
    assert "no Grok session" in capsys.readouterr().err


def test_billing_json_is_grok_s_result_unchanged(fake, tmp_path, capsys):
    billing = {"config": {"creditUsagePercent": 63.0, "currentPeriod": {"end": "2026-10-23T11:55:37Z"}},
               "subscription_tier": "Free"}
    s = fake.state()
    s["billing"] = billing
    fake.write(s)
    assert cli.main(["grok", "billing", "--home", str(_home(tmp_path, "gh")), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == billing


def test_billing_text(fake, tmp_path, capsys):
    home = _home(tmp_path, "gh")
    s = fake.state()
    s["billing"] = {"config": {"creditUsagePercent": 63.0, "currentPeriod": {"end": "2026-10-23"}},
                    "subscription_tier": "Free"}
    fake.write(s)
    assert cli.main(["grok", "billing", "--home", str(home)]) == 0
    assert capsys.readouterr().out.strip() == f"{home}: Free, 63% of credit used, period ends 2026-10-23"


def test_billing_of_a_logged_out_home_exits_one(fake, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    assert cli.main(["grok", "billing", "--home", str(_home(tmp_path, "gh", login=False))]) == 1
    assert "grok login" in capsys.readouterr().err


def test_no_grok_on_path_is_said_plainly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(acp.shutil, "which", lambda name: None)
    assert cli.main(["grok", "billing", "--home", str(_home(tmp_path, "gh"))]) == 1
    assert "grok is not on PATH" in capsys.readouterr().err
