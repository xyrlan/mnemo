"""`mnemo disable-rule <slug>` — flip runtime: false on a rule's frontmatter."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mnemo.cli.parser import command
from mnemo.core import config, paths
from mnemo.core.filters import derive_rule_slug, parse_frontmatter


def _find_rule_file(vault_root: Path, slug: str) -> Path | None:
    shared = vault_root / "shared"
    if not shared.is_dir():
        return None
    for md in shared.rglob("*.md"):
        # Match by filesystem stem first (most reliable for slug-as-filename)
        if md.stem == slug:
            return md
        # Also match by derive_rule_slug in case frontmatter slug/name matches
        try:
            text = md.read_text()
        except OSError:
            continue
        fm = parse_frontmatter(text)
        if fm and derive_rule_slug(fm, md.stem) == slug:
            return md
    return None


def run_disable_rule(vault_root: Path, *, slug: str) -> int:
    md = _find_rule_file(vault_root, slug)
    if md is None:
        print(f"error: rule not found for slug {slug!r}", file=sys.stderr)
        return 2
    text = md.read_text()
    if not text.startswith("---\n"):
        print(f"error: {md} has no frontmatter", file=sys.stderr)
        return 2
    end = text.find("\n---\n", 4)
    if end == -1:
        print(f"error: {md} frontmatter not closed", file=sys.stderr)
        return 2
    fm_block = text[4:end]
    body = text[end + 5:]
    if "\nruntime: false" in "\n" + fm_block or fm_block.startswith("runtime: false"):
        print(f"already disabled: {md.relative_to(vault_root)}")
        return 0
    fm_lines = [ln for ln in fm_block.splitlines() if ln.strip() != "runtime: true"]
    fm_lines.append("runtime: false")
    new_text = "---\n" + "\n".join(fm_lines) + "\n---\n" + body
    md.write_text(new_text)
    print(f"disabled: {md.relative_to(vault_root)}")
    return 0


@command("disable-rule")
def _cmd(ns: argparse.Namespace) -> int:
    cfg = config.load_config()
    vault = paths.vault_root(cfg)
    return run_disable_rule(vault, slug=ns.slug)
