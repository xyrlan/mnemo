"""Integration tests for PreToolUse hook registration in settings.json.

Verifies:
- inject_hooks registers a PreToolUse entry with the correct matcher and command
- SessionStart and SessionEnd are still registered (regression check)
- uninject_hooks / _strip_mnemo_entries removes mnemo-owned PreToolUse entries
  while preserving user-owned entries in the same event bucket
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.install import settings as inj


def test_inject_hooks_registers_pretooluse_with_matcher(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"

    inj.inject_hooks(settings_path)

    data = json.loads(settings_path.read_text())
    hooks = data["hooks"]

    # PreToolUse must be present
    assert "PreToolUse" in hooks, "PreToolUse key missing from hooks"
    entries = hooks["PreToolUse"]
    assert entries, "PreToolUse entry list is empty"

    # Find the mnemo-owned entry
    mnemo_entries = [
        e for e in entries
        if any(inj.MNEMO_TAG in h.get("command", "") for h in e.get("hooks", []))
    ]
    assert len(mnemo_entries) == 1, (
        f"Expected exactly 1 mnemo PreToolUse entry, got {len(mnemo_entries)}"
    )
    entry = mnemo_entries[0]

    # Matcher must match the spec
    assert entry.get("matcher") == "Bash|Edit|Write|MultiEdit", (
        f"matcher mismatch: {entry.get('matcher')!r}"
    )

    # Command must target the correct module
    hook_cmds = [h.get("command", "") for h in entry.get("hooks", [])]
    assert any(cmd.endswith("-m mnemo.hooks.pre_tool_use") for cmd in hook_cmds), (
        f"No hook command ending with '-m mnemo.hooks.pre_tool_use'; got: {hook_cmds}"
    )

    # Regression: SessionStart and SessionEnd must still be registered
    assert "SessionStart" in hooks, "SessionStart missing after inject"
    assert "SessionEnd" in hooks, "SessionEnd missing after inject"


def test_strip_mnemo_entries_removes_pretooluse_on_uninstall(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    user_cmd = "/usr/local/bin/my-tool --hook pre-tool"

    # Build a settings dict with:
    #   - a mnemo-owned PreToolUse entry (the kind inject_hooks creates)
    #   - a user-owned PreToolUse entry with a different command
    initial = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash|Edit|Write|MultiEdit",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"/usr/bin/python3 -m mnemo.hooks.pre_tool_use",
                        }
                    ],
                },
                {
                    "hooks": [
                        {"type": "command", "command": user_cmd}
                    ]
                },
            ]
        }
    }
    settings_path.write_text(json.dumps(initial))

    inj.uninject_hooks(settings_path)

    data = json.loads(settings_path.read_text())
    hooks = data.get("hooks", {})

    # User-owned entry must survive
    pretooluse_entries = hooks.get("PreToolUse", [])
    remaining_cmds = [
        h.get("command", "")
        for e in pretooluse_entries
        for h in e.get("hooks", [])
    ]
    assert any(user_cmd in c for c in remaining_cmds), (
        f"User-owned PreToolUse entry was incorrectly removed; remaining: {remaining_cmds}"
    )

    # Mnemo-owned entry must be gone
    assert not any(inj.MNEMO_TAG in c for c in remaining_cmds), (
        f"Mnemo PreToolUse entry survived uninject; commands: {remaining_cmds}"
    )


def test_strip_mnemo_entries_removes_pretooluse_key_when_only_mnemo(tmp_home: Path):
    """If the only PreToolUse entry was mnemo-owned, the key should be absent after uninject."""
    settings_path = tmp_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    initial = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash|Edit|Write|MultiEdit",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "/usr/bin/python3 -m mnemo.hooks.pre_tool_use",
                        }
                    ],
                }
            ]
        }
    }
    settings_path.write_text(json.dumps(initial))

    inj.uninject_hooks(settings_path)

    data = json.loads(settings_path.read_text())
    hooks = data.get("hooks", {})

    # PreToolUse key must be absent (or empty list) — match existing convention
    assert "PreToolUse" not in hooks or hooks["PreToolUse"] == [], (
        f"PreToolUse key should be gone after uninject; got: {hooks.get('PreToolUse')}"
    )
