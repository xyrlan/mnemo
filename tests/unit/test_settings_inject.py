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
    # v0.3.1 removed UserPromptSubmit (legacy write-only logger) and PostToolUse.
    # v0.8.0 re-introduced UserPromptSubmit — now a *read* hook (Prompt Reflex
    # BM25F rule injection). PostToolUse remains gone.
    assert "SessionStart" in hooks
    assert "SessionEnd" in hooks
    assert "UserPromptSubmit" in hooks
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
    """Migration regression: an existing settings.json with legacy mnemo
    entries for modules that no longer exist must have those stale command
    strings pruned on `mnemo init` — otherwise the hook loads a ghost module
    and ImportErrors on every session.

    - PostToolUse (v0.3.0 writer) was removed in v0.3.1 and never came back.
    - UserPromptSubmit was removed in v0.3.1 (legacy `user_prompt` module) and
      re-introduced in v0.8.0 pointing at the new `user_prompt_submit` module.
      The legacy `/py -m mnemo.hooks.user_prompt` command must not survive
      migration; the new module must be registered fresh in its place.
    """
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

    # PostToolUse stays gone.
    assert "PostToolUse" not in hooks

    # UserPromptSubmit now exists (v0.8.0) but must point at the NEW module,
    # not the legacy one.
    assert "UserPromptSubmit" in hooks
    ups_cmds = [
        h.get("command", "")
        for entry in hooks["UserPromptSubmit"]
        for h in entry.get("hooks", [])
    ]
    assert all("mnemo.hooks.user_prompt" not in c or "user_prompt_submit" in c for c in ups_cmds), (
        f"legacy /py -m mnemo.hooks.user_prompt command survived migration: {ups_cmds}"
    )
    assert any("user_prompt_submit" in c for c in ups_cmds), (
        f"new user_prompt_submit module not registered: {ups_cmds}"
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
