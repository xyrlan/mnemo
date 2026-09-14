"""``mnemo stale`` — live rules that cite a file no longer in the repo (#274).

    mnemo stale            # this repo, read-only
    mnemo stale --json     # machine-readable
    mnemo stale --why      # why identifiers are counted, not checked

Read-only. There is no ``--apply``: see :mod:`mnemo.core.stale` for why the
finding is a pointer for a human to re-read the rule, not something to stamp.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command


@command("stale")
def cmd_stale(args: argparse.Namespace) -> int:
    import json as _json
    import os

    from mnemo import cli
    from mnemo.core import stale as ST
    from mnemo.core.agent import resolve_canonical_agent

    if getattr(args, "why", False):
        print(ST.WHY, end="")
        return 0

    vault = cli._resolve_vault()
    cwd = os.getcwd()
    agent = resolve_canonical_agent(cwd)
    project = getattr(args, "project", None) or agent.name
    repo_root = getattr(args, "repo", None) or agent.repo_root
    if not agent.has_git and not getattr(args, "repo", None):
        print(
            f"{cwd} is not in a git repository — `mnemo stale` checks rules "
            f"against a repo at HEAD. Run it inside the project, or pass --repo."
        )
        return 2

    report = ST.run(vault, project=project, repo_root=repo_root,
                    ref=getattr(args, "ref", None) or "HEAD")

    if getattr(args, "json", False):
        print(_json.dumps({
            "project": report.project,
            "repo_root": report.repo_root,
            "ref": report.ref,
            "pages_scanned": report.pages_scanned,
            "stale_pages": len(report.stale_pages),
            "rate": round(report.rate, 4),
            "paths_resolved": report.paths_resolved,
            "paths_skipped": report.paths_skipped,
            "identifiers_skipped": report.identifiers_skipped,
            "findings": [
                {
                    "slug": f.slug,
                    "page": str(f.page),
                    "missing": [
                        {"span": c.span, "path": c.path, "line": c.line, "moved_to": moved}
                        for c, moved in f.missing
                    ],
                }
                for f in report.stale_pages
            ],
        }, indent=2))
        return 0

    print(ST.format_report(report))
    return 0
