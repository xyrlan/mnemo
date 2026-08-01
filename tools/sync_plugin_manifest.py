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


def _sync_plugin_commands(commands_dir: Path) -> None:
    """Regenerate the plugin's commands/ directory from PLUGIN_COMMANDS."""
    from mnemo.install.settings import PLUGIN_COMMANDS, render_plugin_command

    commands_dir.mkdir(parents=True, exist_ok=True)
    expected = {f"{name}.md" for name in PLUGIN_COMMANDS}
    for name, spec in PLUGIN_COMMANDS.items():
        (commands_dir / f"{name}.md").write_text(render_plugin_command(spec))
    # Drop files for commands that no longer exist, so a rename can't leave a
    # stale command behind that still invokes a subcommand we removed.
    for stale in commands_dir.glob("*.md"):
        if stale.name not in expected:
            stale.unlink()


def sync(repo_root: Path, version: str) -> None:
    sys.path.insert(0, str(repo_root / "src"))

    plugin_dir = repo_root / ".claude-plugin"
    manifest_path = plugin_dir / "plugin.json"
    data = json.loads(manifest_path.read_text())
    data["version"] = version
    # The manifest used to carry a `commands` array. Claude Code discovers
    # commands from the commands/ directory instead, and every entry in that
    # array invoked `python3 -m mnemo`, which a plugin install has no way to
    # run. Generating the directory (below) replaces it.
    data.pop("commands", None)
    manifest_path.write_text(json.dumps(data, indent=2) + "\n")

    _sync_plugin_commands(repo_root / "commands")
    _sync_marketplace(plugin_dir / "marketplace.json", version)


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent
    import re
    pyproject_text = (repo_root / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject_text, re.MULTILINE)
    version = m.group(1) if m else "0.0.0"
    sync(repo_root, version)
    print(f".claude-plugin/{{plugin,marketplace}}.json regenerated (version {version})")
