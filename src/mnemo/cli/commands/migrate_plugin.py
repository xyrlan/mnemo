"""``mnemo migrate-plugin`` — remove a pre-plugin install's hooks.

Backs the plugin's ``/mnemo:migrate`` skill. Only the hooks in settings.json go
away; the vault, and everything captured in it, is untouched.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from mnemo.cli.parser import command
from mnemo.install import migration


@command("migrate-plugin")
def cmd_migrate_plugin(args: argparse.Namespace) -> int:
    candidates = [
        Path.home() / ".claude" / "settings.json",
        Path(os.getcwd()) / ".claude" / "settings.json",
    ]

    legacy = migration.find_legacy_installs(candidates)
    if not legacy:
        print("No pre-plugin install found — nothing to migrate.")
        return 0

    print("Found mnemo hooks installed outside the plugin:")
    for path in legacy:
        print(f"  {path}")

    if getattr(args, "dry_run", False):
        print("\n--dry-run: nothing changed.")
        return 0

    changed = migration.migrate(legacy)
    print(f"\nRemoved mnemo hooks from {len(changed)} file(s). A timestamped")
    print("backup sits next to each one. Your vault was not touched.")
    print("\nThe plugin's own hooks are unaffected — mnemo keeps working.")
    return 0
