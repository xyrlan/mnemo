"""Runtime entry points for the mnemo CLI.

Hosts :func:`main` (argparse → dispatch), :func:`_resolve_vault`
(monkeypatched by recall + telemetry tests), and :func:`_run_open`
(platform-aware ``open``/``xdg-open``/``startfile`` shim used by the
``open`` command).
"""
from __future__ import annotations

import sys
from pathlib import Path

from mnemo.cli.parser import COMMANDS, _build_parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if e.code is not None else 2
    # Bare ``mnemo`` (no subcommand) shows the orientation card — a short list
    # of common commands. ``mnemo help`` and ``mnemo --help`` still print the
    # full argparse dump for users who explicitly ask for it.
    if args.command is None:
        return _print_landing()
    fn = COMMANDS.get(args.command)
    if fn is None:
        print(f"unknown command: {args.command}", file=sys.stderr)
        return 2
    try:
        return fn(args)
    except KeyboardInterrupt:
        return 130


def _print_landing() -> int:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version
    try:
        v = _pkg_version("mnemo-claude")
    except PackageNotFoundError:
        v = "unknown"
    print(f"mnemo {v} — the Obsidian that populates itself")
    print()
    print("  mnemo init       first-run setup")
    print("  mnemo status     vault + hook health + recent activity")
    print("  mnemo doctor     full diagnostic with fixes")
    print("  mnemo open       open the vault in Obsidian")
    print("  mnemo autopilot  autonomous monitoring + self-fix")
    print()
    print("  mnemo help       all commands")
    return 0


def _resolve_vault() -> Path:
    from mnemo.core import config as cfg_mod, paths as paths_mod
    cfg = cfg_mod.load_config()
    return paths_mod.vault_root(cfg)


def _run_open(path: Path) -> None:
    import subprocess
    import os
    if sys.platform.startswith("darwin"):
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(path)], check=False)
