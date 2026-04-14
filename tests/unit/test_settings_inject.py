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
    # v0.3.1 removed UserPromptSubmit and PostToolUse — they were write-only
    # log amplifiers feeding a consumer that no longer exists.
    assert "SessionStart" in hooks
    assert "SessionEnd" in hooks
    assert "UserPromptSubmit" not in hooks
    assert "PostToolUse" not in hooks


def test_hook_command_is_directly_executable(tmp_home: Path):
    """Regression: the hook command must NOT be prefixed with a marker token
    that breaks shell dispatch. Claude Code runs `command` literally; an entry
    like 'mnemo: /path/to/python -m ...' would fail with command-not-found."""
    import os
    import re

    settings_path = tmp_home / ".claude" / "settings.json"
    settings.inject_hooks(settings_path)
    data = json.loads(settings_path.read_text())
    # An "executable token" must be either:
    #  - an absolute POSIX path: /usr/bin/python3
    #  - an absolute Windows path: C:\Python311\python.exe (or with forward slashes)
    #  - a bare command name resolvable on PATH: python3, python
    abs_posix = re.compile(r"^/")
    abs_windows = re.compile(r"^[A-Za-z]:[\\/]")
    bare_name = re.compile(r"^python3?(\.exe)?$")
    for event in ("SessionStart", "SessionEnd"):
        for entry in data["hooks"][event]:
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                first_token = cmd.split()[0] if cmd else ""
                executable = (
                    abs_posix.match(first_token)
                    or abs_windows.match(first_token)
                    or bare_name.match(os.path.basename(first_token))
                )
                assert executable, (
                    f"{event} command first token is not directly executable: "
                    f"{first_token!r} (full command: {cmd!r})"
                )


def test_inject_strips_legacy_removed_hooks(tmp_home: Path):
    """v0.3.1 migration: an existing settings.json with legacy UserPromptSubmit
    and PostToolUse entries (from v0.3.0) should have them pruned when the
    user runs `mnemo init` again, because those hook modules no longer exist
    and leaving the registration causes ImportError on every session start."""
    settings_path = tmp_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    legacy = {
        "hooks": {
            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "/py -m mnemo.hooks.user_prompt"}]}],
            "PostToolUse": [{"matcher": "Write|Edit", "hooks": [{"type": "command", "command": "/py -m mnemo.hooks.post_tool_use"}]}],
        }
    }
    settings_path.write_text(json.dumps(legacy))
    settings.inject_hooks(settings_path)
    data = json.loads(settings_path.read_text())
    hooks = data.get("hooks", {})
    assert "UserPromptSubmit" not in hooks
    assert "PostToolUse" not in hooks


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
