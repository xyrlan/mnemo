"""mnemo init must register UserPromptSubmit; uninstall must clean it."""
from __future__ import annotations

import json

from mnemo.install import settings


def test_hook_definitions_include_user_prompt_submit():
    assert "UserPromptSubmit" in settings.HOOK_DEFINITIONS
    defn = settings.HOOK_DEFINITIONS["UserPromptSubmit"]
    assert defn["module"] == "user_prompt_submit"
    assert defn["matcher"] is None


def test_inject_writes_user_prompt_submit_entry(tmp_path):
    sp = tmp_path / "settings.json"
    settings.inject_hooks(sp)
    data = json.loads(sp.read_text())
    assert "UserPromptSubmit" in data["hooks"]


def test_uninject_removes_user_prompt_submit_entry(tmp_path):
    sp = tmp_path / "settings.json"
    settings.inject_hooks(sp)
    settings.uninject_hooks(sp)
    data = json.loads(sp.read_text())
    assert "UserPromptSubmit" not in data.get("hooks", {})
