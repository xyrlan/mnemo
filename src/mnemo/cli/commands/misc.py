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
    if not args.yes:
        try:
            answer = input("Remove mnemo hooks from settings.json? Vault data is preserved. [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Aborted.", file=sys.stderr)
            return 2
    settings_path = Path(os.path.expanduser("~/.claude/settings.json"))
    claude_json_path = Path(os.path.expanduser("~/.claude.json"))
    try:
        vault = cli._resolve_vault()
        inj.uninject_statusline(settings_path, vault)
        inj.uninject_hooks(settings_path)
        inj.uninject_mcp_servers(claude_json_path)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print("Hooks, MCP server, and statusLine removed. Vault preserved.")
    return 0
