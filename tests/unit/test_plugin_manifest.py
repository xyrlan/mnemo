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
