from __future__ import annotations

from pathlib import Path


def test_hosts_check_is_silent_ok_with_no_extra_hosts(tmp_home: Path, tmp_path: Path, capsys):
    from mnemo.cli.commands.doctor_checks.hosts import _doctor_check_hosts

    assert _doctor_check_hosts(tmp_path) is True
    assert capsys.readouterr().out == ""


def test_hosts_check_flags_a_missing_command(tmp_home: Path, tmp_path: Path, capsys):
    from mnemo.cli.commands.doctor_checks.hosts import _doctor_check_hosts

    cfg = tmp_home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[mcp_servers.mnemo]\ncommand = "/nonexistent/python"\nargs = ["-m", "mnemo", "mcp-server"]\n', encoding="utf-8")
    assert _doctor_check_hosts(tmp_path) is False
    out = capsys.readouterr().out
    assert "✗ codex" in out and "/nonexistent/python" in out and "mnemo init --host codex" in out


def test_hosts_check_ok_line_for_a_good_registration(tmp_home: Path, tmp_path: Path, capsys, monkeypatch):
    from mnemo.cli.commands.doctor_checks.hosts import _doctor_check_hosts
    from mnemo.hosts import get_host

    monkeypatch.chdir(tmp_path)
    get_host("cursor").register_mcp(project=False, cwd=tmp_path)
    assert _doctor_check_hosts(tmp_path) is True
    assert "✓ cursor" in capsys.readouterr().out


def test_hosts_check_reports_both_global_and_project_registrations(tmp_home: Path, tmp_path: Path, capsys, monkeypatch):
    from mnemo.cli.commands.doctor_checks.hosts import _doctor_check_hosts
    from mnemo.hosts import get_host

    monkeypatch.chdir(tmp_path)
    host = get_host("cursor")
    host.register_mcp(project=False, cwd=tmp_path)
    host.register_mcp(project=True, cwd=tmp_path)
    assert _doctor_check_hosts(tmp_path) is True
    out = capsys.readouterr().out
    assert out.count("✓ cursor") == 2
    assert str(tmp_home / ".cursor" / "mcp.json") in out
    assert str(tmp_path / ".cursor" / "mcp.json") in out


def test_doctor_registry_includes_hosts():
    from mnemo.cli.commands.doctor import DOCTOR_CHECKS

    assert "hosts" in [name for name, _ in DOCTOR_CHECKS]
