"""``mnemo init --host`` / ``mnemo uninstall --host`` — the non-Claude branch.

Cursor and Codex get the vault, an MCP registration and a rules file, and
nothing else: no hooks, no status line, no memory mirror. The Claude default
path is untouched, which the last test here pins.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from mnemo import cli
from tests.unit._export_fixtures import write_rule


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "app"
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


def test_parser_accepts_host_on_init_and_uninstall():
    from mnemo.cli.parser import _build_parser

    ns = _build_parser().parse_args(["init", "--host", "cursor", "--yes"])
    assert ns.host == "cursor"
    assert _build_parser().parse_args(["init", "--yes"]).host == "claude"
    assert _build_parser().parse_args(["uninstall", "--host", "codex", "--yes"]).host == "codex"


def test_init_cursor_registers_mcp_scaffolds_vault_and_exports(tmp_home: Path, repo: Path, capsys):
    vault = tmp_home / "v"
    # a rule for this project must exist before init so the export has content
    write_rule(vault, slug="use-yarn-not-npm", projects=("app",))
    rc = cli.main(["init", "--host", "cursor", "--yes", "--vault-root", str(vault), "--no-mirror"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "mnemo" in json.loads((tmp_home / ".cursor" / "mcp.json").read_text())["mcpServers"]
    assert (repo / ".cursor" / "rules" / "mnemo.mdc").exists()
    assert "exported 1 rule" in out
    assert "learns from Claude Code sessions" in out
    # no Claude hooks were touched
    assert not (tmp_home / ".claude" / "settings.json").exists()


def test_init_cursor_with_empty_vault_says_what_to_do(tmp_home: Path, repo: Path, capsys):
    rc = cli.main(["init", "--host", "cursor", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "no rules yet" in out and "mnemo export --host cursor" in out
    assert not (repo / ".cursor" / "rules" / "mnemo.mdc").exists()


def test_init_codex_project_is_a_usage_error(tmp_home: Path, repo: Path, capsys):
    rc = cli.main(["init", "--host", "codex", "--project", "--yes"])
    assert rc == 2
    assert "no project-level MCP config" in capsys.readouterr().err


def test_init_codex_without_binary_prints_snippet_and_succeeds(tmp_home: Path, repo: Path, capsys, monkeypatch):
    monkeypatch.setattr("mnemo.hosts.codex.shutil.which", lambda name: None)
    rc = cli.main(["init", "--host", "codex", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "[mcp_servers.mnemo]" in captured.out


def test_uninstall_cursor_removes_only_the_cursor_entry(tmp_home: Path, repo: Path, capsys):
    cli.main(["init", "--host", "cursor", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror"])
    rc = cli.main(["uninstall", "--host", "cursor", "--yes"])
    assert rc == 0
    data = json.loads((tmp_home / ".cursor" / "mcp.json").read_text())
    assert "mnemo" not in data.get("mcpServers", {})
    assert "cursor" in capsys.readouterr().out


def test_init_host_claude_is_still_the_default_path(tmp_home: Path, repo: Path):
    rc = cli.main([
        "init", "--host", "claude", "--yes",
        "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet",
    ])
    assert rc == 0
    assert (tmp_home / ".claude" / "settings.json").exists()


def test_init_cursor_survives_a_broken_rules_file(tmp_home: Path, repo: Path, capsys, monkeypatch):
    """The MCP server is already registered; a rules-file failure is not fatal."""
    import mnemo.core.export as export_mod

    def boom(*a, **k):
        raise export_mod.MarkerError("hand-edited block")

    monkeypatch.setattr(export_mod, "run_export", boom)
    rc = cli.main(["init", "--host", "cursor", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "rules file not written" in captured.err
    assert "mnemo export --host cursor" in captured.err
    assert "mnemo" in json.loads((tmp_home / ".cursor" / "mcp.json").read_text())["mcpServers"]


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX chmod semantics not honored on Windows")
@pytest.mark.skipif(os.geteuid() == 0 if hasattr(os, "geteuid") else False, reason="root ignores file modes")
def test_init_cursor_ignores_an_unwritable_claude_settings(tmp_home: Path, repo: Path):
    """Cursor never writes ~/.claude/settings.json, so it must not gate on it."""
    settings = tmp_home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{}")
    settings.chmod(0o444)
    try:
        rc = cli.main(["init", "--host", "cursor", "--yes",
                       "--vault-root", str(tmp_home / "v"), "--no-mirror"])
        assert rc == 0
        assert (tmp_home / ".cursor" / "mcp.json").exists()
    finally:
        settings.chmod(0o600)


def test_codex_paste_snippet_survives_quiet(tmp_home: Path, repo: Path, capsys, monkeypatch):
    """--quiet must not swallow work the user still has to do by hand."""
    monkeypatch.setattr("mnemo.hosts.codex.shutil.which", lambda name: None)
    rc = cli.main(["init", "--host", "codex", "--yes", "--quiet",
                   "--vault-root", str(tmp_home / "v"), "--no-mirror"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[mcp_servers.mnemo]" in out
    # ...while the ordinary progress chatter still is suppressed
    assert "Running preflight checks" not in out


def test_init_cursor_prints_the_user_page_privacy_note(tmp_home: Path, repo: Path, capsys):
    vault = tmp_home / "v"
    write_rule(vault, page_type="user", slug="prefers-terse-replies", projects=("app",))
    rc = cli.main(["init", "--host", "cursor", "--yes", "--vault-root", str(vault), "--no-mirror"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "user-profile page(s) included" in err
    assert "prefers-terse-replies" in err
