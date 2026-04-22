"""`mnemo dedup-rules` — consolidate shared/*.md files sharing the same `name:`.

Dry-run by default (like `migrate-worktree-briefings`). Use `--apply` to
execute the plan: canonical = most sources[] (tie → newer extracted_at);
duplicates deleted; sources[] + frontmatter project(s) unioned on canonical.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command


@command("dedup-rules")
def cmd_dedup_rules(args: argparse.Namespace) -> int:
    from mnemo import cli
    from mnemo.core.dedup_rules import plan_dedup
    from mnemo.core.filters import parse_frontmatter

    vault = cli._resolve_vault()
    plan = plan_dedup(vault)

    if not plan.groups:
        print("no duplicates found")
        return 0

    print(f"{len(plan.groups)} group(s) with duplicate names:\n")
    for g in plan.groups:
        canon_fm = parse_frontmatter(g.canonical.read_text(encoding="utf-8"))
        name = canon_fm.get("name", "")
        canon_rel = g.canonical.relative_to(vault)
        dup_rels = ", ".join(p.name for p in g.duplicates)
        print(f"  '{name}'")
        print(f"    keep:   {canon_rel}")
        print(f"    delete: {dup_rels}")
        print(f"    merged_sources: {len(g.merged_sources)}  projects: {g.merged_projects}")

    if not getattr(args, "apply", False):
        print("\n(dry-run — pass --apply to execute)")
        return 0

    plan.apply()
    print("\napplied.")
    return 0
