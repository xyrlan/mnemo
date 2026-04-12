from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.install import settings


def test_inject_into_empty_settings(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings.inject_hooks(settings_path)
    data = json.loads(settings_path.read_text())
    hooks = data["hooks"]
    assert "SessionStart" in hooks
    assert "SessionEnd" in hooks
    assert "UserPromptSubmit" in hooks
    assert "PostToolUse" in hooks
    # PostToolUse must have matcher Write|Edit
    pt = hooks["PostToolUse"][0]
    assert pt["matcher"] == "Write|Edit"


def test_hook_command_is_directly_executable(tmp_home: Path):
    """Regression: the hook command must NOT be prefixed with a marker token
    that breaks shell dispatch. Claude Code runs `command` literally; an entry
    like 'mnemo: /path/to/python -m ...' would fail with command-not-found."""
    settings_path = tmp_home / ".claude" / "settings.json"
    settings.inject_hooks(settings_path)
    data = json.loads(settings_path.read_text())
    for event in ("SessionStart", "SessionEnd", "UserPromptSubmit", "PostToolUse"):
        for entry in data["hooks"][event]:
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                # Must start with an absolute path or 'python3', never with a tag word + colon.
                first_token = cmd.split()[0] if cmd else ""
                assert first_token.startswith("/") or first_token == "python3", (
                    f"{event} command starts with non-executable token: {first_token!r} "
                    f"(full command: {cmd!r})"
                )
                assert ":" not in first_token, (
                    f"{event} command first token contains ':' which breaks shell dispatch: "
                    f"{first_token!r}"
                )


def test_inject_creates_backup(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"existing": True}))
    settings.inject_hooks(settings_path)
    backups = list(settings_path.parent.glob("settings.json.bak.*"))
    assert len(backups) == 1


def test_inject_preserves_other_plugin_hooks(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    other = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "other-plugin-hook"}]}],
        }
    }
    settings_path.write_text(json.dumps(other))
    settings.inject_hooks(settings_path)
    data = json.loads(settings_path.read_text())
    starts = data["hooks"]["SessionStart"]
    cmds = [
        h["command"]
        for entry in starts
        for h in entry.get("hooks", [])
    ]
    assert any("other-plugin-hook" in c for c in cmds)
    assert any("mnemo" in c for c in cmds)


def test_inject_idempotent(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings.inject_hooks(settings_path)
    settings.inject_hooks(settings_path)
    data = json.loads(settings_path.read_text())
    starts = data["hooks"]["SessionStart"]
    mnemo_count = sum(
        1
        for entry in starts
        for h in entry.get("hooks", [])
        if "mnemo" in h.get("command", "")
    )
    assert mnemo_count == 1


def test_uninject_removes_only_mnemo(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    other = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "other-plugin-hook"}]}],
        }
    }
    settings_path.write_text(json.dumps(other))
    settings.inject_hooks(settings_path)
    settings.uninject_hooks(settings_path)
    data = json.loads(settings_path.read_text())
    cmds = [
        h["command"]
        for entry in data["hooks"].get("SessionStart", [])
        for h in entry.get("hooks", [])
    ]
    assert any("other-plugin-hook" in c for c in cmds)
    assert not any("mnemo" in c for c in cmds)


def test_inject_aborts_on_malformed_settings(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{not json")
    with pytest.raises(settings.SettingsError):
        settings.inject_hooks(settings_path)
