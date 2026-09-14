"""``mnemo publish`` — write this repo's rules into the repo for another vault to import.

Prints one line per outcome on stdout, caveats on stderr, and nothing else;
every decision lives in :mod:`mnemo.core.share.publish`. Exit 2 for a usage
error, 1 when a write failed, 0 otherwise — a slug another vault already
published is the tree's condition, not this run's failure, so it is a
counted ``refused`` line and exit 0 (backfill's environmental split is the
precedent).

The tree lands in the repo the user is standing in, at ``.mnemo-shared/``;
``--project`` only changes which project's rules are selected, as it does
for ``mnemo export``.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mnemo.cli.parser import command


@command("publish")
def cmd_publish(args: argparse.Namespace) -> int:
    from mnemo import cli  # late binding, as every other command does
    from mnemo.cli.commands.export import configured_universal_threshold, print_export_notes
    from mnemo.core import export as export_mod
    from mnemo.core.agent import resolve_agent, resolve_canonical_agent
    from mnemo.core.share import format as share_format
    from mnemo.core.share import publish as publish_mod

    vault = cli._resolve_vault()
    cwd = os.getcwd()
    # Project follows a worktree back to its main repo (the hooks and export
    # do the same, so the manifest and the reflex agree); the tree lands in
    # the checkout the user is actually standing in.
    project = getattr(args, "project", None) or resolve_canonical_agent(cwd).name
    repo_root = Path(resolve_agent(cwd).repo_root).resolve()
    share_dir = share_format.SHARE_DIR

    raw_types = str(getattr(args, "types", None) or ",".join(publish_mod.DEFAULT_TYPES))
    types = tuple(t.strip() for t in raw_types.split(",") if t.strip())
    if not types:
        print("error: --types cannot be empty", file=sys.stderr)
        return 2
    unknown = [t for t in types if t not in export_mod.ALL_TYPES]
    if unknown:
        print(
            f"error: unknown page type(s): {', '.join(unknown)}; "
            f"choose from {', '.join(export_mod.ALL_TYPES)}",
            file=sys.stderr,
        )
        return 2

    dry_run = bool(getattr(args, "dry_run", False))
    remove = bool(getattr(args, "remove", False))
    try:
        report = publish_mod.run_publish(
            vault, project=project, repo_root=repo_root, types=types,
            dry_run=dry_run, today=None,
            universal_threshold=configured_universal_threshold(), remove=remove,
        )
    except OSError as exc:
        print(f"error: could not write {share_dir}: {exc}", file=sys.stderr)
        return 1

    if remove:
        n = len(report.pruned)
        if not report.removed:
            print(f"nothing to remove at {share_dir}")
            return 0
        verb = "would remove" if dry_run else "removed"
        for rel in report.pruned:
            print(f"{verb} {share_dir}/{rel}")
        print(f"{verb} {n} {'file' if n == 1 else 'files'} of this vault's from {share_dir} and the manifest")
        return 0

    if not report.rules:
        print(f"no rules to publish for {project} — correct Claude, run `mnemo learn`, then publish")
        return 0

    print_export_notes(report)
    if publish_mod.share_dir_is_gitignored(repo_root):
        print(
            f"warning: {share_dir} is ignored by this repo's .gitignore — "
            "the published tree cannot be committed until that rule is removed",
            file=sys.stderr,
        )
    if report.not_pages:
        n = len(report.not_pages)
        print(
            f"note: {n} file(s) in {share_dir} are not mnemo pages, left alone: "
            + ", ".join(report.not_pages),
            file=sys.stderr,
        )

    for rel, why in report.refused:
        print(f"refused {share_dir}/{rel}: {why}")

    n = report.published
    plural = "rule" if n == 1 else "rules"
    summary = (
        f"{n} {plural} ({report.universal} universal) → {share_dir}: "
        f"{len(report.written)} new, {len(report.updated)} updated, "
        f"{len(report.unchanged)} unchanged, {len(report.pruned)} pruned"
        + (f", {len(report.refused)} refused" if report.refused else "")
    )
    if dry_run:
        for rel in report.written:
            print(f"would write {share_dir}/{rel}")
        for rel in report.updated:
            print(f"would update {share_dir}/{rel}")
        for rel in report.pruned:
            print(f"would prune {share_dir}/{rel}")
        print(f"would publish {summary}")
        return 0

    print(f"published {summary}")
    if report.changed:
        print(f"commit {share_dir} and push; a teammate runs `mnemo import` to review them")
    else:
        print("nothing changed since the last publish")
    print("re-run after new rules; `mnemo status` says when it is stale")
    return 0
