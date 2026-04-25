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


def test_init_installs_statusline_composer(tmp_home: Path):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"])
    settings = json.loads((tmp_home / ".claude" / "settings.json").read_text())
    assert "statusLine" in settings
    assert settings["statusLine"]["command"].endswith("statusline-compose")


def test_init_preserves_user_statusline_via_composer(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps({
        "statusLine": {"type": "command", "command": "/home/user/my-prompt.sh"},
    }))

    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"])

    # statusLine in settings.json now points at composer
    data = json.loads(settings_path.read_text())
    assert data["statusLine"]["command"].endswith("statusline-compose")
    # Original captured in mnemo state
    state_path = tmp_home / "vault" / ".mnemo" / "statusline-original.json"
    state = json.loads(state_path.read_text())
    assert state["command"] == "/home/user/my-prompt.sh"


def test_uninstall_restores_user_statusline(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps({
        "statusLine": {"type": "command", "command": "/home/user/my-prompt.sh"},
    }))

    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"])
    cli.main(["uninstall", "--yes"])

    data = json.loads(settings_path.read_text())
    # Original restored
    assert data["statusLine"]["command"] == "/home/user/my-prompt.sh"


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


# --- v0.12: project-scoped install (`--project` / `--local`) ---


def _project_workspace(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a clean cwd inside tmp_home and chdir into it."""
    proj = tmp_home / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    return proj


def test_init_project_writes_local_only(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    proj = _project_workspace(tmp_home, monkeypatch)
    rc = cli.main(["init", "--project", "--yes", "--no-mirror", "--quiet"])
    assert rc == 0
    assert (proj / ".claude" / "settings.json").exists()
    assert (proj / ".mcp.json").exists()
    assert (proj / ".mnemo").is_dir()
    assert (proj / ".mnemo" / "mnemo.config.json").exists()
    settings = json.loads((proj / ".claude" / "settings.json").read_text())
    assert "SessionStart" in settings["hooks"]
    mcp = json.loads((proj / ".mcp.json").read_text())
    assert "mnemo" in mcp["mcpServers"]


def test_init_project_does_not_touch_home(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    _project_workspace(tmp_home, monkeypatch)
    assert cli.main(["init", "--project", "--yes", "--no-mirror", "--quiet"]) == 0
    assert not (tmp_home / ".claude" / "settings.json").exists()
    assert not (tmp_home / ".claude.json").exists()
    assert not (tmp_home / "mnemo" / "mnemo.config.json").exists()


def test_init_project_idempotent(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    proj = _project_workspace(tmp_home, monkeypatch)
    args = ["init", "--project", "--yes", "--no-mirror", "--quiet"]
    assert cli.main(args) == 0
    assert cli.main(args) == 0
    mcp = json.loads((proj / ".mcp.json").read_text())
    assert list(mcp["mcpServers"].keys()).count("mnemo") == 1


def test_init_project_warns_on_global_coexistence(
    tmp_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "global-vault"), "--no-mirror", "--quiet"])
    capsys.readouterr()

    proj = _project_workspace(tmp_home, monkeypatch)
    rc = cli.main(["init", "--project", "--yes", "--no-mirror"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "WARNING" in captured.out and "global mnemo install" in captured.out
    assert (proj / ".claude" / "settings.json").exists()


def test_init_project_appends_gitignore(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    proj = _project_workspace(tmp_home, monkeypatch)
    (proj / ".gitignore").write_text("# pre-existing\nnode_modules/\n")
    cli.main(["init", "--project", "--yes", "--no-mirror", "--quiet"])
    text = (proj / ".gitignore").read_text()
    assert "node_modules/" in text
    assert ".claude/" in text
    assert ".mnemo/" in text
    cli.main(["init", "--project", "--yes", "--no-mirror", "--quiet"])
    text2 = (proj / ".gitignore").read_text()
    assert text2.count(".claude/") == 1
    assert text2.count(".mnemo/") == 1


def test_uninstall_project_cleans_local_only(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    proj = _project_workspace(tmp_home, monkeypatch)
    cli.main(["init", "--project", "--yes", "--no-mirror", "--quiet"])
    rc = cli.main(["uninstall", "--project", "--yes"])
    assert rc == 0
    settings = json.loads((proj / ".claude" / "settings.json").read_text())
    for entries in settings.get("hooks", {}).values():
        for entry in entries:
            for h in entry.get("hooks", []):
                assert "mnemo.hooks." not in h.get("command", "")
    mcp = json.loads((proj / ".mcp.json").read_text())
    assert "mnemo" not in mcp.get("mcpServers", {})
    assert (proj / ".mnemo").is_dir()


def test_resolve_vault_prefers_local_config(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "global-vault"), "--no-mirror", "--quiet"])
    proj = _project_workspace(tmp_home, monkeypatch)
    cli.main(["init", "--project", "--yes", "--no-mirror", "--quiet"])

    from mnemo.core import config as cfg_mod
    cfg = cfg_mod.load_config()
    assert Path(cfg["vaultRoot"]) == proj / ".mnemo"
