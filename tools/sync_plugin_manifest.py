"""Regenerate .claude-plugin/plugin.json from mnemo.install.settings.SLASH_COMMANDS.

Plugin manifest is the alternative entry point for users who install via
/plugin marketplace. SLASH_COMMANDS in install/settings.py is the source
of truth for what commands mnemo exposes; this script keeps the manifest
aligned so the two install paths produce the same surface.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def sync(repo_root: Path, version: str) -> None:
    sys.path.insert(0, str(repo_root / "src"))
    from mnemo.install.settings import SLASH_COMMANDS

    manifest_path = repo_root / ".claude-plugin" / "plugin.json"
    data = json.loads(manifest_path.read_text())
    data["version"] = version
    data["commands"] = [
        {"name": name, "description": spec["description"], "command": spec["command"]}
        for name, spec in SLASH_COMMANDS.items()
    ]
    manifest_path.write_text(json.dumps(data, indent=2) + "\n")


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent
    import re
    pyproject_text = (repo_root / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject_text, re.MULTILINE)
    version = m.group(1) if m else "0.0.0"
    sync(repo_root, version)
    print(f".claude-plugin/plugin.json regenerated (version {version})")
