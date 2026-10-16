"""``mnemo.install.grok_hooks``: mnemo's hooks in a Grok hook file."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.install import grok_hooks, settings as inj


def test_the_file_carries_settings_json_s_entries_exactly():
    hooks = grok_hooks.render()["hooks"]
    assert list(hooks) == list(inj.HOOK_DEFINITIONS)
    for event, defn in inj.HOOK_DEFINITIONS.items():
        assert hooks[event] == [inj._build_entry(event, defn)]
        command = hooks[event][0]["hooks"][0]["command"]
        assert inj.is_mnemo_hook_command(command)


def test_event_names_are_the_ones_grok_inspect_reports():
    assert grok_hooks.expected_events() == [
        "session_start", "user_prompt_submit", "pre_tool_use", "session_end"]


def test_paths_global_and_project(tmp_path: Path):
    assert grok_hooks.hook_file(tmp_path / "g") == tmp_path / "g" / "hooks" / "mnemo.json"
    assert grok_hooks.hook_file(tmp_path / "g", project=True, cwd=tmp_path / "p") == (
        tmp_path / "p" / ".grok" / "hooks" / "mnemo.json")


def test_install_writes_json_and_is_idempotent(tmp_path: Path):
    file = tmp_path / "g" / "hooks" / "mnemo.json"
    grok_hooks.install(file)
    first = file.read_bytes()
    assert grok_hooks.is_installed(file)
    assert json.loads(first) == grok_hooks.render()
    grok_hooks.install(file)
    assert file.read_bytes() == first


def test_install_keeps_a_hook_the_user_added_to_the_file(tmp_path: Path):
    file = tmp_path / "mnemo.json"
    mine = {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-guard"}]}
    file.write_text(json.dumps({"hooks": {"PreToolUse": [mine]}, "other": 1}), encoding="utf-8")
    grok_hooks.install(file)
    data = json.loads(file.read_text(encoding="utf-8"))
    assert data["other"] == 1
    assert data["hooks"]["PreToolUse"][0] == mine
    assert len(data["hooks"]["PreToolUse"]) == 2

    assert grok_hooks.uninstall(file) is True
    data = json.loads(file.read_text(encoding="utf-8"))
    assert data == {"hooks": {"PreToolUse": [mine]}, "other": 1}


def test_uninstall_deletes_a_file_left_empty(tmp_path: Path):
    file = tmp_path / "hooks" / "mnemo.json"
    grok_hooks.install(file)
    assert grok_hooks.uninstall(file) is True
    assert not file.exists()
    assert grok_hooks.uninstall(file) is False


def test_install_refuses_a_file_that_is_not_a_json_object(tmp_path: Path):
    file = tmp_path / "mnemo.json"
    file.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(inj.SettingsError):
        grok_hooks.install(file)
    assert file.read_text(encoding="utf-8") == "[1, 2]"
    file.write_text("{not json", encoding="utf-8")
    with pytest.raises(inj.SettingsError):
        grok_hooks.install(file)
    assert not grok_hooks.is_installed(file)
    assert grok_hooks.uninstall(file) is False
