from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from mnemo import cli


def test_init_yes_creates_vault_and_injects(tmp_home: Path, capsys: pytest.CaptureFixture):
    rc = cli.main(["init", "--yes", "--vault-root", str(tmp_home / "vault")])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    vault = tmp_home / "vault"
    assert (vault / "HOME.md").exists()
    assert (vault / "mnemo.config.json").exists()
    settings_path = tmp_home / ".claude" / "settings.json"
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert "SessionStart" in data["hooks"]
    # v0.5: MCP server registered in ~/.claude.json (separate file from settings.json)
    claude_json_path = tmp_home / ".claude.json"
    assert claude_json_path.exists()
    mcp_data = json.loads(claude_json_path.read_text())
    assert "mnemo" in mcp_data["mcpServers"]
    assert mcp_data["mcpServers"]["mnemo"]["args"] == ["-m", "mnemo", "mcp-server"]


def test_init_idempotent_for_mcp_server(tmp_home: Path):
    args = ["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"]
    assert cli.main(args) == 0
    assert cli.main(args) == 0
    mcp_data = json.loads((tmp_home / ".claude.json").read_text())
    # Still exactly one mnemo entry after two inits
    assert list(mcp_data["mcpServers"].keys()).count("mnemo") == 1


def test_uninstall_removes_mcp_server_entry(tmp_home: Path):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"])
    rc = cli.main(["uninstall", "--yes"])
    assert rc == 0
    claude_json_path = tmp_home / ".claude.json"
    if claude_json_path.exists():
        data = json.loads(claude_json_path.read_text())
        # mcpServers either gone entirely or no mnemo entry
        assert "mnemo" not in data.get("mcpServers", {})


def test_init_idempotent(tmp_home: Path):
    args = ["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"]
    assert cli.main(args) == 0
    assert cli.main(args) == 0


def test_init_no_mirror_skips_claude_sync(tmp_home: Path):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    # No bots/<agent>/memory dirs should have been created from a sync.
    bots = tmp_home / "v" / "bots"
    assert bots.exists()
    assert not any(bots.iterdir())


def test_init_quiet_suppresses_stdout(tmp_home: Path, capsys: pytest.CaptureFixture):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    out = capsys.readouterr().out
    assert out.strip() == ""


def test_init_interactive_uses_default_when_blank(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    answers = iter(["", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    rc = cli.main(["init"])
    assert rc == 0
    default_vault = tmp_home / "mnemo"
    assert default_vault.exists()


def test_init_interactive_aborts_on_no(tmp_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    answers = iter([str(tmp_home / "v"), "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    rc = cli.main(["init"])
    assert rc != 0
    captured = capsys.readouterr()
    assert "abort" in (captured.out + captured.err).lower()
