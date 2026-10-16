"""``mnemo doctor``'s Grok section: version, homes, visibility, leader socket."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict

import pytest

from mnemo.cli.commands.doctor_checks import grok as check
from tests.unit.test_host_grok import claude_hooks, claude_mcp, inspect_json

SECRET = "sk-live-do-not-print-0123456789"
REAL_SOCKET_LIMIT = check.socket_limit


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch):
    for name in ("GROK_HOME", "GROK_LEADER_SOCKET", "MNEMO_AGENT", "GROK_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    # pytest's tmp dirs are long enough to break macOS's 103 bytes on their own;
    # the tests about the limit put the real one back.
    monkeypatch.setattr(check, "socket_limit", lambda platform=None: 10_000)


class Grok:
    def __init__(self) -> None:
        self.by_home: Dict[str, Any] = {}
        self.default = inspect_json()
        self.fail_for: set = set()
        self.version = "grok 1.0.60 (01a8e1c591c0)"
        self.calls = []

    def run(self, argv, **kw):
        home = (kw.get("env") or {}).get("GROK_HOME", "")
        self.calls.append((list(argv), home, kw.get("cwd")))
        if argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout=self.version + "\n", stderr="")
        if home in self.fail_for:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="no")
        data = self.by_home.get(home, self.default)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(data), stderr="")


@pytest.fixture
def grok(monkeypatch: pytest.MonkeyPatch) -> Grok:
    fake = Grok()
    monkeypatch.setattr("mnemo.hosts.grok.shutil.which", lambda n: "/bin/grok" if n == "grok" else None)
    monkeypatch.setattr("mnemo.hosts.grok.subprocess.run", fake.run)
    return fake


def make_home(path: Path, *, login: bool = True) -> Path:
    path.mkdir(parents=True)
    if login:
        (path / "auth.json").write_text(json.dumps({"token": SECRET}), encoding="utf-8")
    return path


def run(capsys, tmp_path: Path):
    ok = check._doctor_check_grok(tmp_path)
    return ok, capsys.readouterr().out


def test_silent_when_grok_is_not_installed(tmp_home: Path, tmp_path: Path, monkeypatch, capsys):
    make_home(tmp_home / ".grok")
    monkeypatch.setattr("mnemo.hosts.grok.shutil.which", lambda n: None)
    ok, out = run(capsys, tmp_path)
    assert ok is True and out == ""


def test_everything_wired_reports_version_home_hooks_mcp_and_socket(tmp_home: Path, tmp_path: Path, grok, capsys):
    home = make_home(tmp_home / ".grok")
    grok.default = inspect_json(hooks=claude_hooks(str(tmp_home / ".claude")),
                                mcp=claude_mcp(str(tmp_home / ".claude.json")))
    ok, out = run(capsys, tmp_path)
    assert ok is True
    assert "grok 1.0.60 (01a8e1c591c0) on PATH" in out
    assert f"Grok home {home}: logged in" in out
    assert "mnemo hooks visible to Grok (session_start, user_prompt_submit, pre_tool_use, session_end)" in out
    assert "mnemo MCP server visible to Grok" in out
    assert "leader socket fits" in out
    assert "✗" not in out


def test_the_login_secret_is_never_printed(tmp_home: Path, tmp_path: Path, grok, capsys):
    make_home(tmp_home / ".grok")
    ok, out = run(capsys, tmp_path)
    assert SECRET not in out


def test_a_home_without_login_is_noted_not_failed(tmp_home: Path, tmp_path: Path, grok, capsys):
    make_home(tmp_home / ".grok", login=False)
    grok.default = inspect_json(hooks=claude_hooks(), mcp=claude_mcp())
    ok, out = run(capsys, tmp_path)
    assert ok is True and "not logged in" in out


def test_an_empty_auth_file_is_not_a_login(tmp_home: Path):
    home = make_home(tmp_home / ".grok", login=False)
    (home / "auth.json").write_text("", encoding="utf-8")
    assert check.logged_in(home) is False
    assert check.logged_in(tmp_home / "missing") is False


def test_hooks_and_mcp_missing_are_warnings_with_the_fix(tmp_home: Path, tmp_path: Path, grok, capsys):
    make_home(tmp_home / ".grok")
    ok, out = run(capsys, tmp_path)
    assert ok is False
    assert "mnemo hooks are not visible to Grok" in out
    assert "mnemo MCP server is not visible to Grok" in out
    assert out.count("mnemo init --host grok") == 2


def test_hooks_off_through_compat_switch_do_not_count(tmp_home: Path, tmp_path: Path, grok, capsys):
    make_home(tmp_home / ".grok")
    grok.default = inspect_json(hooks=claude_hooks(status="disabled"), mcp=claude_mcp())
    ok, out = run(capsys, tmp_path)
    assert ok is False and "mnemo hooks are not visible" in out
    assert "MCP server visible" in out


def test_a_partial_hook_set_names_what_is_missing(tmp_home: Path, tmp_path: Path, grok, capsys):
    make_home(tmp_home / ".grok")
    grok.default = inspect_json(hooks=claude_hooks()[:2], mcp=claude_mcp())
    ok, out = run(capsys, tmp_path)
    assert ok is False and "lack: pre_tool_use, session_end" in out


def test_each_home_is_checked_with_its_own_grok_home(tmp_home: Path, tmp_path: Path, grok, capsys):
    a = make_home(tmp_home / ".grok")
    b = make_home(tmp_home / ".grok-work")
    grok.by_home[str(a)] = inspect_json(hooks=claude_hooks(), mcp=claude_mcp())
    # b: nothing wired
    ok, out = run(capsys, tmp_path)
    assert ok is False
    assert f"Grok home {a}: logged in" in out and f"Grok home {b}: logged in" in out
    assert f"mnemo hooks are not visible to Grok in {b}\n" in out
    assert f"mnemo hooks are not visible to Grok in {a}\n" not in out
    inspected = {home for argv, home, _ in grok.calls if argv[1] == "inspect"}
    assert inspected == {str(a), str(b)}


def test_an_inspect_failure_is_reported_not_raised(tmp_home: Path, tmp_path: Path, grok, capsys):
    home = make_home(tmp_home / ".grok")
    grok.fail_for.add(str(home))
    ok, out = run(capsys, tmp_path)
    assert ok is False and "`grok inspect --json` failed" in out


def test_no_home_yet_is_information(tmp_home: Path, tmp_path: Path, grok, capsys):
    ok, out = run(capsys, tmp_path)
    assert ok is True and "no Grok home yet" in out and "grok login" in out


# --- the leader socket limit --------------------------------------------------------


def test_socket_limits_per_platform():
    assert REAL_SOCKET_LIMIT("darwin") == 103
    assert REAL_SOCKET_LIMIT("linux") == 107


def test_a_home_whose_socket_path_is_too_long_is_a_warning(tmp_home: Path, tmp_path: Path, grok, monkeypatch, capsys):
    monkeypatch.setattr(check, "socket_limit", REAL_SOCKET_LIMIT)
    monkeypatch.setattr(check.sys, "platform", "darwin")
    long_home = make_home(tmp_home / ("a" * 90) / ".grok-acct")
    grok.default = inspect_json(hooks=claude_hooks(), mcp=claude_mcp())
    monkeypatch.setenv("GROK_HOME", str(long_home))
    ok, out = run(capsys, tmp_path)
    sock = str(long_home / "leader.sock")
    assert len(sock.encode("utf-8")) > 103
    assert ok is False
    assert f"leader socket {sock} is {len(sock.encode('utf-8'))} bytes; this platform allows 103" in out


def test_the_socket_check_follows_grok_leader_socket(tmp_home: Path, tmp_path: Path, grok, monkeypatch, capsys):
    monkeypatch.setattr(check, "socket_limit", REAL_SOCKET_LIMIT)
    monkeypatch.setattr(check.sys, "platform", "darwin")
    long_home = make_home(tmp_home / ("a" * 90) / ".grok-acct")
    grok.default = inspect_json(hooks=claude_hooks(), mcp=claude_mcp())
    monkeypatch.setenv("GROK_HOME", str(long_home))
    monkeypatch.setenv("GROK_LEADER_SOCKET", "/tmp/mnemo-l.sock")
    ok, out = run(capsys, tmp_path)
    assert ok is True and "leader socket fits" in out


# --- wiring -----------------------------------------------------------------------------


def test_the_check_is_in_the_doctor_registry_after_hosts():
    from mnemo.cli.commands import doctor

    names = [n for n, _ in doctor.DOCTOR_CHECKS]
    assert names.index("grok") == names.index("hosts") + 1
    assert dict(doctor.DOCTOR_CHECKS)["grok"] is check._doctor_check_grok


def test_doctor_stays_silent_about_grok_where_it_is_not_installed(tmp_home: Path, tmp_path: Path, monkeypatch, capsys):
    from mnemo import cli

    monkeypatch.setattr("mnemo.hosts.grok.shutil.which", lambda n: None)
    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_home / "v")
    (tmp_home / "v").mkdir()
    cli.main(["doctor"])
    assert "Grok" not in capsys.readouterr().out
