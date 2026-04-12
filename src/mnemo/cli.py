"""mnemo command-line entry point."""
from __future__ import annotations

import argparse
import sys
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
