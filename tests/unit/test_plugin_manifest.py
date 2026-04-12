from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_plugin_json_well_formed():
    data = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    assert data["name"] == "mnemo"
    assert data["version"]
    assert "commands" in data and isinstance(data["commands"], list)
    expected_cmds = {"init", "status", "doctor", "open", "promote", "compile", "fix", "uninstall", "help"}
    cmd_names = {c.get("name") for c in data["commands"]}
    assert expected_cmds.issubset(cmd_names)


def test_marketplace_json_well_formed():
    data = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
    assert data["name"]
    assert "plugins" in data
