"""``mnemo export`` — write this project's rules where another tool will read them.

Prints one line per outcome and nothing else; the reasoning lives in
:mod:`mnemo.core.export`.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mnemo.cli.parser import command


@command("export")
def cmd_export(args: argparse.Namespace) -> int:
    from mnemo import cli  # late binding, as every other command does
    from mnemo.core import config as cfg_mod
    from mnemo.core import export as export_mod
    from mnemo.core.agent import resolve_agent, resolve_canonical_agent

    vault = cli._resolve_vault()
    cwd = os.getcwd()
    # The project name follows a worktree back to its main repo (the hooks do
    # the same, so the manifest and the reflex agree); the file lands in the
    # tree the user is actually standing in.
    project = getattr(args, "project", None) or resolve_canonical_agent(cwd).name
    repo_root = Path(resolve_agent(cwd).repo_root)
    host = getattr(args, "host", "claude")
    target = getattr(args, "target", "auto")

    # Resolve the target up front so a bad host/target pair is unambiguously
    # a usage error (exit 2), distinct from a write-time failure (exit 1)
    # that run_export raises for the same exception types.
    try:
        export_mod.target_for(host, target, repo_root)
    except export_mod.TargetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    all_types = bool(getattr(args, "all_types", False))
    types = export_mod.ALL_TYPES if all_types else tuple(
        t.strip() for t in str(getattr(args, "types", "feedback,user")).split(",") if t.strip()
    )
    try:
        threshold = int((cfg_mod.load_config().get("scoping") or {}).get("universalThreshold", 2))
    except Exception:  # noqa: BLE001
        threshold = 2

    remove = bool(getattr(args, "remove", False))
    try:
        report = export_mod.run_export(
            vault, project=project, repo_root=repo_root,
            host=host, target=target,
            types=types, universal_threshold=threshold,
            limit=getattr(args, "limit", None),
            dry_run=bool(getattr(args, "dry_run", False)),
            remove=remove,
            force_warning=all_types,
        )
    except (export_mod.TargetError, export_mod.MarkerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    rel = report.target.path.relative_to(repo_root).as_posix()
    if remove:
        print(f"removed {rel}" if report.removed else f"nothing to remove at {rel}")
        return 0
    if not report.rules:
        print(f"no rules to export for {project} — correct Claude, run `mnemo learn`, then export")
        return 0
    if report.warning:
        print(f"warning: {report.warning}", file=sys.stderr)
    n = len(report.rules)
    plural = "rule" if n == 1 else "rules"
    if not report.wrote:
        print(report.block, end="")
        print(f"would write {n} {plural} ({report.universal} universal) → {rel}")
        return 0
    print(f"exported {n} {plural} ({report.universal} universal) → {rel}")
    print("re-run after new rules; `mnemo status` says when it is stale")
    return 0
