"""``mnemo init`` — first-run setup (idempotent).

Two install scopes are supported:

- *Global* (default) — writes ``~/.claude/settings.json`` + ``~/.claude.json``;
  mnemo runs in every Claude Code session.
- *Project* (``--project`` / ``--local``) — writes ``<cwd>/.claude/settings.json``
  + ``<cwd>/.mcp.json`` and forces the vault into ``<cwd>/.mnemo/``. mnemo
  only loads when Claude Code is launched in that directory.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mnemo.cli.parser import command


GITIGNORE_ENTRIES = (".claude/", ".mnemo/")


def _has_global_mnemo_install(home_settings: Path) -> bool:
    """True if ``~/.claude/settings.json`` already wires mnemo hooks."""
    if not home_settings.exists():
        return False
    try:
        data = json.loads(home_settings.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        return False
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            for h in (entry or {}).get("hooks", []) or []:
                if "mnemo.hooks." in (h or {}).get("command", ""):
                    return True
    return False


def _ensure_gitignore(cwd: Path, entries: tuple[str, ...] = GITIGNORE_ENTRIES) -> None:
    """Append missing mnemo entries to ``<cwd>/.gitignore`` (idempotent)."""
    gi = cwd / ".gitignore"
    existing_lines: list[str] = []
    if gi.exists():
        try:
            existing_lines = gi.read_text().splitlines()
        except OSError:
            return
    have = {line.strip() for line in existing_lines}
    missing = [e for e in entries if e not in have]
    if not missing:
        return
    block = ["", "# mnemo (project-scoped install)"] + list(missing)
    new_text = "\n".join(existing_lines + block).rstrip("\n") + "\n"
    try:
        gi.write_text(new_text)
    except OSError:
        pass


@command("init")
def cmd_init(args: argparse.Namespace) -> int:
    import os
    from mnemo.core import config as cfg_mod, mirror
    from mnemo.install import preflight, scaffold, settings as inj

    quiet = bool(args.quiet)
    say = (lambda *a, **k: None) if quiet else print
    project = bool(getattr(args, "project", False))
    cwd = Path.cwd()

    # Resolve install targets per scope
    if project:
        target_settings = cwd / ".claude" / "settings.json"
        target_mcp = cwd / ".mcp.json"
    else:
        target_settings = Path(os.path.expanduser("~/.claude/settings.json"))
        target_mcp = Path(os.path.expanduser("~/.claude.json"))

    # Coexistence warn — project install but global is already wired
    if project and _has_global_mnemo_install(Path(os.path.expanduser("~/.claude/settings.json"))):
        say(
            "WARNING: a global mnemo install is already active at ~/.claude/settings.json.\n"
            "         Both will fire in this project — hooks will run twice. Consider running\n"
            "         `mnemo uninstall` first, or proceed if you understand the duplication."
        )
        if not args.yes:
            try:
                confirm = input("Proceed with project install anyway? [y/N]: ").strip().lower()
            except EOFError:
                confirm = ""
            if confirm not in ("y", "yes"):
                print("Aborted by user.", file=sys.stderr)
                return 2

    # 1. Determine vault root
    vault_root: Path
    if args.vault_root:
        vault_root = Path(os.path.expanduser(args.vault_root))
    elif project:
        vault_root = cwd / ".mnemo"
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
    result = preflight.run_preflight(vault_root=vault_root, settings_target=target_settings)
    for issue in result.issues:
        say(f"  [{issue.severity}] {issue.kind}: {issue.message}")
        say(f"       → {issue.remediation}")
    if not result.ok:
        print("Preflight failed. Resolve the issues above and retry.", file=sys.stderr)
        return 1

    # 3. Confirm settings modification (interactive only)
    if not args.yes:
        try:
            confirm = input(f"Modify {target_settings} to install hooks? [y/N]: ").strip().lower()
        except EOFError:
            confirm = ""
        if confirm not in ("y", "yes"):
            print("Aborted by user.", file=sys.stderr)
            return 2

    # 4. Scaffold vault
    say(f"Scaffolding vault at {vault_root}…")
    scaffold.scaffold_vault(vault_root)

    # 4b. Persist vault root.
    # Project installs write `<vault>/mnemo.config.json` (== `<cwd>/.mnemo/mnemo.config.json`),
    # which `default_config_path()` auto-detects via `_find_local_config()`. Global
    # installs keep using the singleton `default_config_path()`.
    if project:
        cfg_mod.save_config({"vaultRoot": str(vault_root)}, path=vault_root / "mnemo.config.json")
    else:
        cfg_mod.save_config({"vaultRoot": str(vault_root)})

    # 5. Inject hooks
    say(f"Injecting hooks into {target_settings}…")
    try:
        inj.inject_hooks(target_settings)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # 5b. Register MCP server (global → ~/.claude.json; project → <cwd>/.mcp.json)
    say(f"Registering MCP server in {target_mcp}…")
    try:
        inj.inject_mcp_servers(target_mcp)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # 5c. Install additive statusLine composer
    say("Installing statusLine composer (additive — preserves your existing line)…")
    try:
        inj.inject_statusline(target_settings, vault_root)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # 5d. Project scope — ignore install artifacts in version control
    if project:
        _ensure_gitignore(cwd)

    # 6. Optional initial mirror
    if not args.no_mirror:
        say("Mirroring existing Claude memories…")
        cfg = cfg_mod.load_config(vault_root / "mnemo.config.json")
        try:
            mirror.mirror_all(cfg)
        except Exception as e:
            say(f"  (mirror skipped: {e})")

    if project:
        say("mnemo is ready (project scope). Open the vault with: mnemo open")
    else:
        say("mnemo is ready. Open the vault with: mnemo open")
    return 0
