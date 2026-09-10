from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_plugin_json_well_formed():
    data = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    assert data["name"] == "mnemo"
    assert data["version"]


def test_plugin_declares_its_surface_by_convention():
    """Claude Code discovers these by path, not from the manifest."""
    assert (REPO / "hooks" / "hooks.json").is_file()
    assert (REPO / ".mcp.json").is_file()
    assert (REPO / "commands").is_dir()
    assert (REPO / "bin" / "launch").is_file()
    assert (REPO / "bin" / "mnemo.cmd").is_file()


def test_plugin_hooks_cover_every_event_mnemo_installs():
    """The plugin and `mnemo init` must wire the same four events."""
    import sys
    sys.path.insert(0, str(REPO / "src"))
    from mnemo.install.settings import HOOK_DEFINITIONS

    hooks = json.loads((REPO / "hooks" / "hooks.json").read_text())["hooks"]
    assert set(hooks) == set(HOOK_DEFINITIONS)
    for event, defn in HOOK_DEFINITIONS.items():
        entry = hooks[event][0]
        assert entry.get("matcher") == defn["matcher"] or (
            defn["matcher"] is None and "matcher" not in entry
        ), f"{event} matcher drifted from HOOK_DEFINITIONS"
        assert f"hook {defn['module']}" in entry["hooks"][0]["command"]


def test_plugin_commands_never_hardcode_an_interpreter():
    for path in (REPO / "commands").glob("*.md"):
        body = path.read_text()
        assert "${CLAUDE_PLUGIN_ROOT}" in body, path.name
        assert "python3" not in body, path.name


def test_marketplace_json_well_formed():
    data = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
    assert data["name"]
    assert "plugins" in data


def test_mcp_json_spawns_through_git_not_bash_or_cmd():
    """Three spawn constraints meet in .mcp.json, and only `git` satisfies all:
    - Claude Code spawns stdio servers without a shell, so `bin/mnemo.cmd`
      (no shebang) is ENOEXEC on POSIX and a `.cmd` is unspawnable on
      Windows;
    - `bash` is not on PATH on a default Git-for-Windows install (only
      Git\\cmd is), so `command: bash` dies with CONNECTION_CLOSED there;
    - `${CLAUDE_PLUGIN_ROOT:-.}` is not expanded by the plugin loader, which
      resolves it to `.` (the project cwd), while the plain form is a literal
      that breaks the dev checkout (#118).
    `git` is on PATH wherever a plugin got cloned, and a `!` alias runs
    through git's own sh — which on Windows carries bash with it — with
    `$CLAUDE_PLUGIN_ROOT` read at runtime, untouched by either expander."""
    data = json.loads((REPO / ".mcp.json").read_text())
    server = data["mcpServers"]["mnemo"]
    assert server["command"] == "git"
    args = server["args"]
    assert args[0] == "-c" and args[1].startswith("alias.mnemo-mcp=!")
    assert args[2] == "mnemo-mcp"
    assert "${CLAUDE_PLUGIN_ROOT" not in " ".join(args)
    assert '"$CLAUDE_PLUGIN_ROOT"' in args[1]
    assert "cygpath" in args[1], "Windows hands the root over as C:\\... — convert it"
    assert "exec bash bin/launch mcp-server" in args[1]


def test_launchers_are_pinned_to_lf():
    """core.autocrlf=true (the Git for Windows default) would otherwise check
    both out with CRLF; sh/bash then fail on the stray \\r and bin/launch,
    which fails open, hides it."""
    attrs = (REPO / ".gitattributes").read_text()
    assert "bin/launch text eol=lf" in attrs
    assert "bin/mnemo.cmd text eol=lf" in attrs
