from __future__ import annotations

from pathlib import Path

from mnemo import cli


def test_status_prints_nothing_about_hosts_when_only_claude(tmp_home: Path, tmp_path: Path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    cli.main(["status"])
    assert "Hosts:" not in capsys.readouterr().out


def test_status_lists_registered_hosts(tmp_home: Path, tmp_path: Path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "--host", "cursor", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    cli.main(["status"])
    out = capsys.readouterr().out
    assert f"Hosts: cursor ({tmp_home / '.cursor' / 'mcp.json'})" in out


def test_status_flags_a_registration_with_a_missing_command(tmp_home: Path, tmp_path: Path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    cfg = tmp_home / ".cursor" / "mcp.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('{"mcpServers": {"mnemo": {"command": "/nonexistent/x"}}}', encoding="utf-8")
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "command missing, see mnemo doctor" in out
