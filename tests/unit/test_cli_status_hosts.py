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


def test_status_finds_plugin_hooks_without_claude_plugin_root(tmp_home, monkeypatch):
    """A plugin user running `mnemo status` in a terminal has no
    CLAUDE_PLUGIN_ROOT; status used to fall through to settings.json and
    report 0/4 hooks on a healthy install."""
    from mnemo.cli.commands import status as status_mod

    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    root = tmp_home / ".claude" / "plugins" / "cache" / "mnemo-marketplace" / "mnemo" / "1.3.3"
    (root / "hooks").mkdir(parents=True)
    (root / "hooks" / "hooks.json").write_text(json.dumps({
        "hooks": {
            ev: [{"hooks": [{"type": "command", "command": "x"}]}]
            for ev in ("SessionStart", "UserPromptSubmit", "PreToolUse", "SessionEnd")
        }
    }))

    assert status_mod._installed_plugin_root() == str(root)
    events = ("SessionStart", "UserPromptSubmit", "PreToolUse", "SessionEnd")
    assert status_mod._count_plugin_hooks(events) == 4


def test_status_reports_no_plugin_when_cache_is_absent(tmp_home, monkeypatch):
    from mnemo.cli.commands import status as status_mod

    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    assert status_mod._installed_plugin_root() is None
    assert status_mod._count_plugin_hooks(("SessionStart",)) is None
