import json
from pathlib import Path

from tools import sync_plugin_manifest


def test_sync_plugin_manifest_uses_slash_commands(tmp_path: Path):
    manifest = tmp_path / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "name": "mnemo", "version": "0.0.0", "description": "x",
        "commands": [],
    }))

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.12.0")

    data = json.loads(manifest.read_text())
    names = [c["name"] for c in data["commands"]]
    assert "init-project" in names
    assert "uninstall-project" in names
    assert data["version"] == "0.12.0"
