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


def test_sync_plugin_manifest_uses_slash_commands(tmp_path: Path):
    manifest = _plugin_dir(tmp_path) / "plugin.json"

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.12.0")

    data = json.loads(manifest.read_text())
    names = [c["name"] for c in data["commands"]]
    assert "init-project" in names
    assert "uninstall-project" in names
    assert data["version"] == "0.12.0"


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
