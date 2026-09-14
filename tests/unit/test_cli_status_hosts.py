from __future__ import annotations

import json
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


def _seed_plugin_cache(tmp_home, version: str = "1.3.3"):
    """Create the plugin's versioned cache tree, including its hooks manifest."""
    root = tmp_home / ".claude" / "plugins" / "cache" / "mnemo-marketplace" / "mnemo" / version
    (root / "hooks").mkdir(parents=True)
    (root / "hooks" / "hooks.json").write_text(json.dumps({
        "hooks": {
            ev: [{"hooks": [{"type": "command", "command": "x"}]}]
            for ev in ("SessionStart", "UserPromptSubmit", "PreToolUse", "SessionEnd")
        }
    }), encoding="utf-8")
    return root


def _seed_plugin_registry(tmp_home, root, version: str = "1.3.3"):
    """Register the plugin the way `claude plugin install` does.

    The cache dir alone is not an install — uninstall leaves it behind — so the
    registry entry is what makes the plugin real.
    """
    reg = tmp_home / ".claude" / "plugins" / "installed_plugins.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps({"version": 2, "plugins": {
        "mnemo@mnemo-marketplace": [
            {"scope": "user", "installPath": str(root), "version": version}
        ]
    }}), encoding="utf-8")


def test_status_finds_plugin_hooks_without_claude_plugin_root(tmp_home, monkeypatch):
    """A plugin user running `mnemo status` in a terminal has no
    CLAUDE_PLUGIN_ROOT; status used to fall through to settings.json and
    report 0/4 hooks on a healthy install."""
    from mnemo.cli.commands import status as status_mod

    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    root = _seed_plugin_cache(tmp_home)
    _seed_plugin_registry(tmp_home, root)

    assert status_mod._installed_plugin_root() == str(root)
    events = ("SessionStart", "UserPromptSubmit", "PreToolUse", "SessionEnd")
    assert status_mod._count_plugin_hooks(events) == 4


def test_status_ignores_cache_dir_left_behind_by_uninstall(tmp_home, monkeypatch):
    """#229: `claude plugin uninstall` leaves the versioned cache tree — hooks.json
    and all — on disk. Globbing the cache reported a phantom "Hooks (plugin): 4/4"
    for a plugin that no longer runs, and that branch also *replaced* the
    settings.json scope lines, hiding the direct install doing the real work."""
    from mnemo.cli.commands import status as status_mod

    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    _seed_plugin_cache(tmp_home)
    # Registry cleaned by the uninstall; only the cache dir survives.
    reg = tmp_home / ".claude" / "plugins" / "installed_plugins.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps({"version": 2, "plugins": {}}), encoding="utf-8")

    assert status_mod._installed_plugin_root() is None
    assert status_mod._count_plugin_hooks(
        ("SessionStart", "UserPromptSubmit", "PreToolUse", "SessionEnd")
    ) is None


def test_status_reports_no_plugin_when_cache_is_absent(tmp_home, monkeypatch):
    from mnemo.cli.commands import status as status_mod

    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    assert status_mod._installed_plugin_root() is None
    assert status_mod._count_plugin_hooks(("SessionStart",)) is None
