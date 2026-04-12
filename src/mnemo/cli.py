"""mnemo command-line entry point."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {}


def command(name: str) -> Callable:
    def deco(fn: Callable[[argparse.Namespace], int]) -> Callable:
        COMMANDS[name] = fn
        return fn
    return deco


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mnemo", description="The Obsidian that populates itself.")
    sub = p.add_subparsers(dest="command")

    init = sub.add_parser("init", help="first-run setup (idempotent)")
    init.add_argument("--yes", "-y", action="store_true", help="skip prompts (for automation)")
    init.add_argument("--vault-root", type=str, default=None, help="override vault location")
    init.add_argument("--no-mirror", action="store_true", help="skip initial Claude memory mirror")
    init.add_argument("--quiet", action="store_true", help="suppress informational output")

    sub.add_parser("status", help="vault state + hook health + recent activity")
    sub.add_parser("doctor", help="full diagnostic with actionable fixes")
    sub.add_parser("open", help="open vault in Obsidian or file manager")
    promote = sub.add_parser("promote", help="promote a note to wiki/sources/")
    promote.add_argument("source", type=str)
    sub.add_parser("compile", help="regenerate wiki/compiled/ from sources")
    sub.add_parser("fix", help="reset circuit breaker")
    uninstall = sub.add_parser("uninstall", help="remove hooks (keeps vault)")
    uninstall.add_argument("--yes", "-y", action="store_true")
    sub.add_parser("help", help="list commands")
    return p


@command("help")
def cmd_help(_args: argparse.Namespace) -> int:
    parser = _build_parser()
    parser.print_help()
    return 0


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

    # 5. Inject hooks
    settings_path = Path(os.path.expanduser("~/.claude/settings.json"))
    say(f"Injecting hooks into {settings_path}…")
    try:
        inj.inject_hooks(settings_path)
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


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if e.code is not None else 2
    name = args.command or "help"
    fn = COMMANDS.get(name)
    if fn is None:
        print(f"unknown command: {name}", file=sys.stderr)
        return 2
    try:
        return fn(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
