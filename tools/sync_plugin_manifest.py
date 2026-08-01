"""Regenerate the .claude-plugin/ manifests from the release version.

Plugin manifest is the alternative entry point for users who install via
/plugin marketplace. SLASH_COMMANDS in install/settings.py is the source
of truth for what commands mnemo exposes; this script keeps the manifest
aligned so the two install paths produce the same surface.

marketplace.json carries its own copy of the version, which is what the
/plugin marketplace UI shows. Nothing used to sync it and it drifted twelve
minors behind, so it is regenerated here too.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


PLUGIN_NAME = "mnemo"


def _sync_marketplace(marketplace_path: Path, version: str) -> None:
    data = json.loads(marketplace_path.read_text())
    entries = [p for p in data.get("plugins", []) if p.get("name") == PLUGIN_NAME]
    if len(entries) != 1:
        raise SystemExit(
            f"Expected exactly one {PLUGIN_NAME!r} entry in {marketplace_path}, "
            f"found {len(entries)}"
        )
    entries[0]["version"] = version
    marketplace_path.write_text(json.dumps(data, indent=2) + "\n")


def sync(repo_root: Path, version: str) -> None:
    sys.path.insert(0, str(repo_root / "src"))
    from mnemo.install.settings import SLASH_COMMANDS

    plugin_dir = repo_root / ".claude-plugin"
    manifest_path = plugin_dir / "plugin.json"
    data = json.loads(manifest_path.read_text())
    data["version"] = version
    data["commands"] = [
        {"name": name, "description": spec["description"], "command": spec["command"]}
        for name, spec in SLASH_COMMANDS.items()
    ]
    manifest_path.write_text(json.dumps(data, indent=2) + "\n")

    _sync_marketplace(plugin_dir / "marketplace.json", version)


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent
    import re
    pyproject_text = (repo_root / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject_text, re.MULTILINE)
    version = m.group(1) if m else "0.0.0"
    sync(repo_root, version)
    print(f".claude-plugin/{{plugin,marketplace}}.json regenerated (version {version})")
