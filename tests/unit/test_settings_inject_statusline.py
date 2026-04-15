"""Tests for v0.5 additive statusLine composer install/uninstall."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mnemo import statusline as sl
from mnemo.install import settings as inj


def _composer_cmd() -> str:
    return f"{sys.executable or 'python3'} -m mnemo statusline-compose"


# --- inject ---


def test_inject_statusline_into_empty_settings(tmp_path: Path, tmp_vault: Path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text("{}")

    inj.inject_statusline(settings, tmp_vault)

    data = json.loads(settings.read_text())
    assert data["statusLine"]["type"] == "command"
    assert data["statusLine"]["command"].endswith("statusline-compose")
    # State file says no original existed
    state = sl.read_state(tmp_vault)
    assert state == {"command": None}


def test_inject_statusline_preserves_user_command(tmp_path: Path, tmp_vault: Path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({
        "statusLine": {"type": "command", "command": "/home/user/my-prompt.sh"},
    }))

    inj.inject_statusline(settings, tmp_vault)

    data = json.loads(settings.read_text())
    # statusLine in settings.json now points at composer
    assert data["statusLine"]["command"].endswith("statusline-compose")
    # Original command captured for restore
    state = sl.read_state(tmp_vault)
    assert state["command"] == "/home/user/my-prompt.sh"
    assert state["type"] == "command"


def test_inject_statusline_idempotent_when_already_composer(tmp_path: Path, tmp_vault: Path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)

    # First install — capture a real original
    settings.write_text(json.dumps({
        "statusLine": {"type": "command", "command": "/home/user/my-prompt.sh"},
    }))
    inj.inject_statusline(settings, tmp_vault)

    # Manually verify state captured the user command
    assert sl.read_state(tmp_vault)["command"] == "/home/user/my-prompt.sh"

    # Second install — must NOT re-capture the composer as the new "original"
    inj.inject_statusline(settings, tmp_vault)

    # State still has the user's command, NOT the composer
    state = sl.read_state(tmp_vault)
    assert state["command"] == "/home/user/my-prompt.sh"
    assert "statusline-compose" not in state["command"]


def test_inject_statusline_preserves_unrelated_keys(tmp_path: Path, tmp_vault: Path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({
        "permissions": {"allow": ["Bash"]},
        "hooks": {"SessionStart": []},
    }))

    inj.inject_statusline(settings, tmp_vault)

    data = json.loads(settings.read_text())
    assert data["permissions"] == {"allow": ["Bash"]}
    assert data["hooks"] == {"SessionStart": []}
    assert "statusLine" in data


def test_inject_statusline_creates_settings_file(tmp_path: Path, tmp_vault: Path):
    settings = tmp_path / ".claude" / "settings.json"
    inj.inject_statusline(settings, tmp_vault)
    assert settings.exists()
    data = json.loads(settings.read_text())
    assert "statusLine" in data


# --- uninject ---


def test_uninject_statusline_restores_original(tmp_path: Path, tmp_vault: Path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({
        "statusLine": {"type": "command", "command": "/home/user/my-prompt.sh"},
    }))

    inj.inject_statusline(settings, tmp_vault)
    inj.uninject_statusline(settings, tmp_vault)

    data = json.loads(settings.read_text())
    assert data["statusLine"]["command"] == "/home/user/my-prompt.sh"
    assert sl.read_state(tmp_vault) is None


def test_uninject_statusline_removes_key_when_no_original(tmp_path: Path, tmp_vault: Path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text("{}")

    inj.inject_statusline(settings, tmp_vault)
    inj.uninject_statusline(settings, tmp_vault)

    data = json.loads(settings.read_text())
    assert "statusLine" not in data
    assert sl.read_state(tmp_vault) is None


def test_uninject_statusline_leaves_user_custom_alone(tmp_path: Path, tmp_vault: Path):
    """If settings.json has a non-mnemo statusLine, uninject leaves it untouched."""
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({
        "statusLine": {"type": "command", "command": "/some/other/prompt.sh"},
    }))

    inj.uninject_statusline(settings, tmp_vault)

    data = json.loads(settings.read_text())
    assert data["statusLine"]["command"] == "/some/other/prompt.sh"


def test_uninject_statusline_noop_when_settings_missing(tmp_path: Path, tmp_vault: Path):
    settings = tmp_path / ".claude" / "settings.json"
    inj.uninject_statusline(settings, tmp_vault)  # must not raise


def test_inject_uninject_round_trip_with_user_command(tmp_path: Path, tmp_vault: Path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    original = {
        "statusLine": {"type": "command", "command": "echo hi"},
        "theme": "dark",
    }
    settings.write_text(json.dumps(original))

    inj.inject_statusline(settings, tmp_vault)
    inj.uninject_statusline(settings, tmp_vault)

    data = json.loads(settings.read_text())
    assert data["statusLine"]["command"] == "echo hi"
    assert data["theme"] == "dark"
