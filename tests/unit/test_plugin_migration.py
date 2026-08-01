"""Detecting a pre-plugin install so hooks don't fire twice.

Anyone who ran `mnemo init` has four hooks in settings.json. Installing the
plugin adds four more via the plugin's own hooks.json — Claude Code fires
both, so every session gets doubled capture, doubled injection, and doubled
enforcement. Plugins cannot write outside their own directory, so this cannot
be fixed silently; it has to be detected and reported.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.install import migration
from mnemo.install.settings import HOOK_DEFINITIONS, inject_hooks


def _settings_with_mnemo_hooks(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    inject_hooks(path)
    return path


def test_no_legacy_install_is_reported_for_a_clean_machine(tmp_path: Path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {}}))

    assert migration.find_legacy_installs([settings]) == []


def test_a_missing_settings_file_is_not_a_legacy_install(tmp_path: Path):
    assert migration.find_legacy_installs([tmp_path / "absent.json"]) == []


def test_detects_hooks_written_by_a_previous_mnemo_init(tmp_path: Path):
    settings = _settings_with_mnemo_hooks(tmp_path / "settings.json")

    assert migration.find_legacy_installs([settings]) == [settings]


def test_reports_every_scope_that_carries_a_legacy_install(tmp_path: Path):
    """A user can have both a global and a project-local install."""
    global_s = _settings_with_mnemo_hooks(tmp_path / "home" / "settings.json")
    project_s = _settings_with_mnemo_hooks(tmp_path / "proj" / "settings.json")

    found = migration.find_legacy_installs([global_s, project_s])

    assert set(found) == {global_s, project_s}


def test_a_malformed_settings_file_is_not_a_crash(tmp_path: Path):
    settings = tmp_path / "settings.json"
    settings.write_text("{ not json")

    assert migration.find_legacy_installs([settings]) == []


def test_third_party_hooks_are_not_mistaken_for_ours(tmp_path: Path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": "/home/x/mnemo-notes/bin/backup.sh"},
        ]}]}
    }))

    assert migration.find_legacy_installs([settings]) == []


def test_migrate_strips_mnemo_hooks_and_leaves_everything_else(tmp_path: Path):
    settings = _settings_with_mnemo_hooks(tmp_path / "settings.json")
    data = json.loads(settings.read_text())
    data["hooks"].setdefault("SessionStart", []).append(
        {"hooks": [{"type": "command", "command": "/usr/local/bin/other-tool"}]}
    )
    data["model"] = "opus"
    settings.write_text(json.dumps(data))

    migration.migrate([settings])

    after = json.loads(settings.read_text())
    remaining = [
        h["command"]
        for entries in after.get("hooks", {}).values()
        for e in entries
        for h in e.get("hooks", [])
    ]
    assert remaining == ["/usr/local/bin/other-tool"]
    assert after["model"] == "opus", "unrelated settings must survive"


def test_migrate_backs_up_before_touching_anything(tmp_path: Path):
    settings = _settings_with_mnemo_hooks(tmp_path / "settings.json")
    before = settings.read_text()

    migration.migrate([settings])

    backups = list(tmp_path.glob("settings.json.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text() == before


def test_migrate_is_idempotent(tmp_path: Path):
    settings = _settings_with_mnemo_hooks(tmp_path / "settings.json")

    migration.migrate([settings])
    first = json.loads(settings.read_text())
    migration.migrate([settings])

    assert json.loads(settings.read_text()) == first


def test_notice_is_emitted_once_then_suppressed(tmp_path: Path):
    state = tmp_path / "state"

    assert migration.should_notify(state) is True
    migration.mark_notified(state)
    assert migration.should_notify(state) is False


def test_notice_text_names_the_command_and_the_consequence(tmp_path: Path):
    settings = _settings_with_mnemo_hooks(tmp_path / "settings.json")

    text = migration.notice([settings])

    assert "twice" in text.lower()
    assert "/mnemo:migrate" in text
    assert str(settings) in text


@pytest.mark.parametrize("event", sorted(HOOK_DEFINITIONS))
def test_every_installed_event_is_detectable(tmp_path: Path, event: str):
    """Detection must not depend on which events happen to be present."""
    settings = tmp_path / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    inject_hooks(settings)
    data = json.loads(settings.read_text())
    data["hooks"] = {event: data["hooks"][event]}
    settings.write_text(json.dumps(data))

    assert migration.find_legacy_installs([settings]) == [settings]
