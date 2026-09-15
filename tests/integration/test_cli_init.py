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
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "SessionStart" in data["hooks"]
    # v0.5: MCP server registered in ~/.claude.json (separate file from settings.json)
    claude_json_path = tmp_home / ".claude.json"
    assert claude_json_path.exists()
    mcp_data = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert "mnemo" in mcp_data["mcpServers"]
    assert mcp_data["mcpServers"]["mnemo"]["args"] == ["-m", "mnemo", "mcp-server"]


def test_init_idempotent_for_mcp_server(tmp_home: Path):
    args = ["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"]
    assert cli.main(args) == 0
    assert cli.main(args) == 0
    mcp_data = json.loads((tmp_home / ".claude.json").read_text(encoding="utf-8"))
    # Still exactly one mnemo entry after two inits
    assert list(mcp_data["mcpServers"].keys()).count("mnemo") == 1


def test_uninstall_removes_mcp_server_entry(tmp_home: Path):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"])
    rc = cli.main(["uninstall", "--yes"])
    assert rc == 0
    claude_json_path = tmp_home / ".claude.json"
    if claude_json_path.exists():
        data = json.loads(claude_json_path.read_text(encoding="utf-8"))
        # mcpServers either gone entirely or no mnemo entry
        assert "mnemo" not in data.get("mcpServers", {})


def test_init_installs_statusline_composer(tmp_home: Path):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"])
    settings = json.loads((tmp_home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "statusLine" in settings
    assert settings["statusLine"]["command"].endswith("statusline-compose")


def test_init_preserves_user_statusline_via_composer(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps({
        "statusLine": {"type": "command", "command": "/home/user/my-prompt.sh"},
    }), encoding="utf-8")

    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"])

    # statusLine in settings.json now points at composer
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert data["statusLine"]["command"].endswith("statusline-compose")
    # Original captured in mnemo state
    state_path = tmp_home / "vault" / ".mnemo" / "statusline-original.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["command"] == "/home/user/my-prompt.sh"


def test_uninstall_restores_user_statusline(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps({
        "statusLine": {"type": "command", "command": "/home/user/my-prompt.sh"},
    }), encoding="utf-8")

    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"])
    cli.main(["uninstall", "--yes"])

    data = json.loads(settings_path.read_text(encoding="utf-8"))
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
    settings = json.loads((proj / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "SessionStart" in settings["hooks"]
    mcp = json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))
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
    mcp = json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))
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
    (proj / ".gitignore").write_text("# pre-existing\nnode_modules/\n", encoding="utf-8")
    cli.main(["init", "--project", "--yes", "--no-mirror", "--quiet"])
    text = (proj / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules/" in text
    assert ".claude/" in text
    assert ".mnemo/" in text
    cli.main(["init", "--project", "--yes", "--no-mirror", "--quiet"])
    text2 = (proj / ".gitignore").read_text(encoding="utf-8")
    assert text2.count(".claude/") == 1
    assert text2.count(".mnemo/") == 1


def test_uninstall_project_cleans_local_only(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    proj = _project_workspace(tmp_home, monkeypatch)
    cli.main(["init", "--project", "--yes", "--no-mirror", "--quiet"])
    rc = cli.main(["uninstall", "--project", "--yes"])
    assert rc == 0
    settings = json.loads((proj / ".claude" / "settings.json").read_text(encoding="utf-8"))
    for entries in settings.get("hooks", {}).values():
        for entry in entries:
            for h in entry.get("hooks", []):
                assert "mnemo.hooks." not in h.get("command", "")
    mcp = json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))
    assert "mnemo" not in mcp.get("mcpServers", {})
    assert (proj / ".mnemo").is_dir()


def test_resolve_vault_prefers_local_config(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "global-vault"), "--no-mirror", "--quiet"])
    proj = _project_workspace(tmp_home, monkeypatch)
    cli.main(["init", "--project", "--yes", "--no-mirror", "--quiet"])

    from mnemo.core import config as cfg_mod
    # The env var (set by the autouse home fixture) short-circuits the lookup;
    # this test is about the fallback order beneath it: local beats global.
    monkeypatch.delenv("MNEMO_CONFIG_PATH", raising=False)
    cfg = cfg_mod.load_config()
    assert Path(cfg["vaultRoot"]) == proj / ".mnemo"


def test_init_registers_slash_commands(tmp_home: Path):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"])
    commands_dir = tmp_home / ".claude" / "commands"
    files = {p.stem for p in commands_dir.glob("*.md")}
    assert "init-project" in files
    assert "init" in files
    init_project = (commands_dir / "init-project.md").read_text(encoding="utf-8")
    from mnemo._selfexec import self_command
    assert f"!`{self_command('init', '--project')}`" in init_project


def test_init_registers_the_packaged_skills(tmp_home: Path):
    """`mnemo init` wrote commands but never skills, so an install that
    skipped the plugin could not load `decomposing-for-dispatch` (#233)."""
    from mnemo.install.settings import SKILLS, SKILL_TAG, read_skill

    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"])

    for name in SKILLS:
        skill = tmp_home / ".claude" / "skills" / name / "SKILL.md"
        assert skill.is_file()
        body = skill.read_text(encoding="utf-8")
        assert body.startswith("---\nname: " + name)
        assert SKILL_TAG in body
        assert body.replace(SKILL_TAG + "\n", "", 1) == read_skill(name)


def test_uninstall_strips_the_skills(tmp_home: Path):
    from mnemo.install.settings import SKILLS

    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"])
    cli.main(["uninstall", "--yes"])

    for name in SKILLS:
        assert not (tmp_home / ".claude" / "skills" / name).exists()


def test_uninstall_strips_slash_commands(tmp_home: Path):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"])
    cli.main(["uninstall", "--yes"])
    commands_dir = tmp_home / ".claude" / "commands"
    files = {p.stem for p in commands_dir.glob("*.md")} if commands_dir.exists() else set()
    assert "init-project" not in files
    assert "init" not in files


# --- #303: re-running init must not reset unrelated config -------------------

def _config_path(tmp_home: Path) -> Path:
    return tmp_home / "mnemo" / "mnemo.config.json"


def test_reinit_keeps_non_default_config(tmp_home: Path):
    vault = tmp_home / "mnemo"
    cfg = _config_path(tmp_home)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({
        "vaultRoot": str(vault),
        "extraction": {"subprocessTimeout": 180},
        "doctor": {"skipStatuslineDrift": True},
    }), encoding="utf-8")

    assert cli.main(["init", "--yes", "--vault-root", str(vault), "--no-mirror", "--quiet"]) == 0

    assert json.loads(cfg.read_text(encoding="utf-8")) == {
        "vaultRoot": str(vault),
        "extraction": {"subprocessTimeout": 180},
        "doctor": {"skipStatuslineDrift": True},
    }


def test_reinit_with_new_vault_root_updates_only_that_key(tmp_home: Path):
    cfg = _config_path(tmp_home)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"vaultRoot": "/old", "extraction": {"subprocessTimeout": 180}}), encoding="utf-8")

    new_vault = tmp_home / "elsewhere"
    assert cli.main(["init", "--yes", "--vault-root", str(new_vault), "--no-mirror", "--quiet"]) == 0

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data == {"vaultRoot": str(new_vault), "extraction": {"subprocessTimeout": 180}}


def test_first_init_writes_the_scaffold_template_not_the_defaults(tmp_home: Path):
    # The merge reads the raw file, so load_config's defaults never get frozen
    # into it; what a fresh vault holds is scaffold's template plus vaultRoot.
    from mnemo.install.scaffold import _read_template

    vault = tmp_home / "mnemo"
    assert cli.main(["init", "--yes", "--vault-root", str(vault), "--no-mirror", "--quiet"]) == 0
    expected = json.loads(_read_template("mnemo.config.json"))
    expected["vaultRoot"] = str(vault)
    assert json.loads(_config_path(tmp_home).read_text(encoding="utf-8")) == expected


def test_init_backs_up_a_config_it_cannot_merge(tmp_home: Path):
    cfg = _config_path(tmp_home)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('{"vaultRoot": "/x", oops', encoding="utf-8")

    vault = tmp_home / "mnemo"
    assert cli.main(["init", "--yes", "--vault-root", str(vault), "--no-mirror", "--quiet"]) == 0

    assert json.loads(cfg.read_text(encoding="utf-8")) == {"vaultRoot": str(vault)}
    backups = list(cfg.parent.glob("mnemo.config.json.bak.*"))
    assert [b.read_text(encoding="utf-8") for b in backups] == ['{"vaultRoot": "/x", oops']


def test_init_project_keeps_non_default_config(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    proj = tmp_home / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    cfg = proj / ".mnemo" / "mnemo.config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"vaultRoot": str(proj / ".mnemo"), "enrichment": {"enabled": False}}), encoding="utf-8")

    assert cli.main(["init", "--project", "--yes", "--no-mirror", "--quiet"]) == 0

    assert json.loads(cfg.read_text(encoding="utf-8"))["enrichment"] == {"enabled": False}


# --- #303: `init --hooks-only` rewrites the hooks and nothing else ------------

_PRE_271 = "Bash|Edit|Write|MultiEdit"


def _age_the_matcher(settings_path: Path) -> dict:
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    for entry in data["hooks"]["PreToolUse"]:
        entry["matcher"] = _PRE_271
    data["statusLine"] = {"type": "command", "command": "/home/user/my-prompt.sh"}
    data["hooks"]["PreToolUse"].append(
        {"matcher": "", "hooks": [{"type": "command", "command": "/opt/other-tool hook"}]}
    )
    settings_path.write_text(json.dumps(data), encoding="utf-8")
    return data


def test_hooks_only_widens_the_matcher_and_touches_nothing_else(tmp_home: Path):
    vault = tmp_home / "mnemo"
    assert cli.main(["init", "--yes", "--vault-root", str(vault), "--no-mirror", "--quiet"]) == 0
    settings_path = tmp_home / ".claude" / "settings.json"
    before = _age_the_matcher(settings_path)
    cfg = _config_path(tmp_home)
    cfg.write_text(json.dumps({"vaultRoot": str(vault), "extraction": {"subprocessTimeout": 180}}), encoding="utf-8")
    mcp_before = (tmp_home / ".claude.json").read_bytes()
    statusline_state = vault / ".mnemo" / "statusline-original.json"
    state_before = statusline_state.read_bytes()

    assert cli.main(["init", "--hooks-only", "--quiet"]) == 0

    after = json.loads(settings_path.read_text(encoding="utf-8"))
    mnemo_matchers = [
        e.get("matcher") for e in after["hooks"]["PreToolUse"]
        if "mnemo" in json.dumps(e["hooks"])
    ]
    assert mnemo_matchers == ["Bash|Read|Edit|Write|MultiEdit"]
    assert {"matcher": "", "hooks": [{"type": "command", "command": "/opt/other-tool hook"}]} in after["hooks"]["PreToolUse"]
    # A user's reverted statusLine stays reverted; a full init would re-wrap it.
    assert after["statusLine"] == before["statusLine"]
    assert statusline_state.read_bytes() == state_before
    assert (tmp_home / ".claude.json").read_bytes() == mcp_before
    assert json.loads(cfg.read_text(encoding="utf-8"))["extraction"] == {"subprocessTimeout": 180}
    assert list(settings_path.parent.glob("settings.json.bak.*"))


def test_hooks_only_refuses_where_nothing_is_installed(tmp_home: Path, capsys: pytest.CaptureFixture):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"statusLine": {"command": "x"}}), encoding="utf-8")

    assert cli.main(["init", "--hooks-only"]) == 1

    assert "run `mnemo init` to install" in capsys.readouterr().err
    assert json.loads(settings_path.read_text(encoding="utf-8")) == {"statusLine": {"command": "x"}}
    assert not (tmp_home / "mnemo").exists()


def test_hooks_only_project_scope(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    proj = tmp_home / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    assert cli.main(["init", "--project", "--yes", "--no-mirror", "--quiet"]) == 0
    local = proj / ".claude" / "settings.json"
    _age_the_matcher(local)

    assert cli.main(["init", "--project", "--hooks-only", "--quiet"]) == 0

    data = json.loads(local.read_text(encoding="utf-8"))
    assert any(e.get("matcher") == "Bash|Read|Edit|Write|MultiEdit" for e in data["hooks"]["PreToolUse"])
    assert not (tmp_home / ".claude" / "settings.json").exists()


def test_hooks_only_rejects_other_hosts(tmp_home: Path, capsys: pytest.CaptureFixture):
    assert cli.main(["init", "--host", "cursor", "--hooks-only"]) == 2
    assert "--hooks-only" in capsys.readouterr().err
