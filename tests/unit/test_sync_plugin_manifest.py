import json
from pathlib import Path

import pytest

from tools import sync_plugin_manifest


def _plugin_dir(tmp_path: Path) -> Path:
    manifest = tmp_path / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "name": "mnemo", "version": "0.0.0", "description": "x",
        "commands": [],
    }))
    (manifest.parent / "marketplace.json").write_text(json.dumps({
        "name": "mnemo-marketplace",
        "plugins": [{"name": "mnemo", "source": "github:xyrlan/mnemo", "version": "0.4.0"}],
    }))
    return manifest.parent


def test_sync_bumps_the_manifest_version(tmp_path: Path):
    manifest = _plugin_dir(tmp_path) / "plugin.json"

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.12.0")

    assert json.loads(manifest.read_text())["version"] == "0.12.0"


def test_sync_drops_the_legacy_commands_array(tmp_path: Path):
    """Claude Code reads commands/ ; the array only invited drift.

    Every entry in it also invoked `python3 -m mnemo`, which a plugin install
    has no way to run.
    """
    manifest = _plugin_dir(tmp_path) / "plugin.json"

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.16.0")

    assert "commands" not in json.loads(manifest.read_text())


def test_sync_generates_the_plugin_command_files(tmp_path: Path):
    _plugin_dir(tmp_path)

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.16.0")

    commands = tmp_path / "commands"
    names = {p.stem for p in commands.glob("*.md")}
    # Exactly the plugin surface — the generated directory IS the slash menu,
    # so an extra file here is an extra entry a user has to read past.
    # init/uninstall have no meaning under a plugin: it declares its own hooks
    # and MCP server, and `/plugin uninstall mnemo` is the uninstall.
    assert names == {"status", "why", "doctor", "learn", "help"}


def test_generated_commands_go_through_the_launcher(tmp_path: Path):
    """Never a resolved path — the plugin is built once, installed everywhere."""
    _plugin_dir(tmp_path)

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.16.0")

    body = (tmp_path / "commands" / "status.md").read_text()
    assert '!`"${CLAUDE_PLUGIN_ROOT}/bin/mnemo.cmd" status`' in body
    assert "python3" not in body


def test_sync_removes_a_command_file_that_no_longer_exists(tmp_path: Path):
    _plugin_dir(tmp_path)
    commands = tmp_path / "commands"
    commands.mkdir()
    (commands / "renamed-away.md").write_text("stale")

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.16.0")

    assert not (commands / "renamed-away.md").exists()


def test_sync_also_bumps_the_marketplace_listing(tmp_path: Path):
    """marketplace.json drifted 12 minors behind because nothing synced it."""
    marketplace = _plugin_dir(tmp_path) / "marketplace.json"

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.16.0")

    entry = json.loads(marketplace.read_text())["plugins"][0]
    assert entry["version"] == "0.16.0"
    assert entry["source"] == "github:xyrlan/mnemo"  # untouched


def test_sync_fails_loudly_when_the_marketplace_lacks_an_mnemo_entry(tmp_path: Path):
    marketplace = _plugin_dir(tmp_path) / "marketplace.json"
    marketplace.write_text(json.dumps({"name": "mnemo-marketplace", "plugins": []}))

    with pytest.raises(SystemExit, match="mnemo"):
        sync_plugin_manifest.sync(repo_root=tmp_path, version="0.16.0")
