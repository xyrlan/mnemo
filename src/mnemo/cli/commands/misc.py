"""Lightweight commands that don't merit their own module.

Hosts ``cmd_help`` (parser-driven help), ``cmd_fix`` (circuit breaker
reset), ``cmd_open`` (open the vault in Obsidian / file manager),
``cmd_mcp_server`` (hidden stdio MCP entry point) and
``cmd_uninstall`` (remove hooks + statusLine; vault preserved).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mnemo.cli.parser import _build_parser, command


@command("help")
def cmd_help(_args: argparse.Namespace) -> int:
    parser = _build_parser()
    parser.print_help()
    return 0


@command("fix")
def cmd_fix(_args: argparse.Namespace) -> int:
    from mnemo import cli  # late binding for monkeypatched _resolve_vault
    from mnemo.core import errors as err_mod
    vault = cli._resolve_vault()
    err_mod.reset(vault)
    print("Circuit breaker reset.")
    return 0


@command("open")
def cmd_open(_args: argparse.Namespace) -> int:
    from mnemo import cli  # late binding for monkeypatched _resolve_vault / _run_open
    vault = cli._resolve_vault()
    cli._run_open(vault)
    print(f"Opened {vault}")
    return 0


@command("mcp-server")
def cmd_mcp_server(_args: argparse.Namespace) -> int:
    """Hidden stdio entry point — wired in ~/.claude.json under mcpServers.mnemo."""
    from mnemo.core.mcp import server as mcp_server
    return mcp_server.serve()


@command("uninstall")
def cmd_uninstall(args: argparse.Namespace) -> int:
    import os
    from mnemo import cli  # late binding for monkeypatched _resolve_vault
    from mnemo.install import settings as inj

    project = bool(getattr(args, "project", False))
    cwd = Path.cwd()
    if project:
        settings_path = cwd / ".claude" / "settings.json"
        mcp_path = cwd / ".mcp.json"
        scope_label = "project-local"
    else:
        settings_path = Path(os.path.expanduser("~/.claude/settings.json"))
        mcp_path = Path(os.path.expanduser("~/.claude.json"))
        scope_label = "global"

    if not args.yes:
        try:
            answer = input(
                f"Remove {scope_label} mnemo hooks ({settings_path})? Vault data is preserved. [y/N]: "
            ).strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Aborted.", file=sys.stderr)
            return 2
    try:
        vault = cli._resolve_vault()
        inj.uninject_statusline(settings_path, vault)
        inj.uninject_hooks(settings_path)
        inj.uninject_mcp_servers(mcp_path)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Hooks, MCP server, and statusLine removed ({scope_label}). Vault preserved.")
    return 0
