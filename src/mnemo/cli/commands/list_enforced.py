"""`mnemo list-enforced` — audit rules that can hard-block tool calls."""
from __future__ import annotations

import argparse
from pathlib import Path

from mnemo.cli.parser import command
from mnemo.core import config, paths
from mnemo.core.filters import parse_frontmatter


def _iter_enforced(vault_root: Path):
    shared = vault_root / "shared"
    if not shared.is_dir():
        return
    for md in sorted(shared.rglob("*.md")):
        try:
            text = md.read_text()
        except OSError:
            continue
        fm = parse_frontmatter(text)
        if not isinstance(fm, dict):
            continue
        enforce = fm.get("enforce")
        if isinstance(enforce, dict) and enforce:
            yield md, fm, enforce


def run_list_enforced(vault_root: Path) -> int:
    any_rule = False
    for md, _fm, enforce in _iter_enforced(vault_root):
        any_rule = True
        rel = md.relative_to(vault_root)
        tool = enforce.get("tool", "?")
        dp = enforce.get("deny_pattern") or enforce.get("deny_patterns")
        dc = enforce.get("deny_command") or enforce.get("deny_commands")
        reason = enforce.get("reason", "")
        print(f"{rel}")
        print(f"  tool: {tool}")
        if dp:
            print(f"  deny_pattern: {dp}")
        if dc:
            print(f"  deny_command: {dc}")
        if reason:
            print(f"  reason: {reason}")
        print()
    if not any_rule:
        print("no enforce blocks found")
    return 0


@command("list-enforced")
def _cmd(_ns: argparse.Namespace) -> int:
    cfg = config.load_config()
    vault = paths.vault_root(cfg)
    return run_list_enforced(vault)
