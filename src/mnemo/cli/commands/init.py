"""``mnemo init`` — first-run setup (idempotent)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mnemo.cli.parser import command


@command("init")
def cmd_init(args: argparse.Namespace) -> int:
    import json
    import os
    from mnemo.core import config as cfg_mod, mirror
    from mnemo.install import preflight, scaffold, settings as inj

    quiet = bool(args.quiet)
    say = (lambda *a, **k: None) if quiet else print

    # 1. Determine vault root
    vault_root: Path
    if args.vault_root:
        vault_root = Path(os.path.expanduser(args.vault_root))
    elif args.yes:
        vault_root = Path(os.path.expanduser("~/mnemo"))
    else:
        try:
            answer = input(f"Vault location [{os.path.expanduser('~/mnemo')}]: ").strip()
        except EOFError:
            answer = ""
        vault_root = Path(os.path.expanduser(answer or "~/mnemo"))

    # 2. Preflight
    say("Running preflight checks…")
    result = preflight.run_preflight(vault_root=vault_root)
    for issue in result.issues:
        say(f"  [{issue.severity}] {issue.kind}: {issue.message}")
        say(f"       → {issue.remediation}")
    if not result.ok:
        print("Preflight failed. Resolve the issues above and retry.", file=sys.stderr)
        return 1

    # 3. Confirm settings.json modification (interactive only)
    if not args.yes:
        try:
            confirm = input("Modify ~/.claude/settings.json to install hooks? [y/N]: ").strip().lower()
        except EOFError:
            confirm = ""
        if confirm not in ("y", "yes"):
            print("Aborted by user.", file=sys.stderr)
            return 2

    # 4. Scaffold vault
    say(f"Scaffolding vault at {vault_root}…")
    scaffold.scaffold_vault(vault_root)

    # 4b. Persist vault root to default config path so _resolve_vault() finds it
    cfg_mod.save_config({"vaultRoot": str(vault_root)})

    # 5. Inject hooks
    settings_path = Path(os.path.expanduser("~/.claude/settings.json"))
    say(f"Injecting hooks into {settings_path}…")
    try:
        inj.inject_hooks(settings_path)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # 5b. Register MCP server in ~/.claude.json (v0.5)
    claude_json_path = Path(os.path.expanduser("~/.claude.json"))
    say(f"Registering MCP server in {claude_json_path}…")
    try:
        inj.inject_mcp_servers(claude_json_path)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # 5c. Install additive statusLine composer (v0.5)
    say("Installing statusLine composer (additive — preserves your existing line)…")
    try:
        inj.inject_statusline(settings_path, vault_root)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # 6. Optional initial mirror
    if not args.no_mirror:
        say("Mirroring existing Claude memories…")
        cfg = cfg_mod.load_config(vault_root / "mnemo.config.json")
        try:
            mirror.mirror_all(cfg)
        except Exception as e:
            say(f"  (mirror skipped: {e})")

    say("mnemo is ready. Open the vault with: mnemo open")
    return 0
