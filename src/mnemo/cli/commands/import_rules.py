"""``mnemo import [PATH]`` — bring a published rules tree into this vault, staged.

``PATH`` defaults to ``<repo_root>/.mnemo-shared`` for the repo you are
standing in (the tree ``mnemo publish`` writes) and may be any directory in
that layout: a sibling clone, a path someone sent. Nothing is promoted —
every rule lands in ``shared/_inbox/<type>/`` for review, and a rule that is
already live gets a ``.proposed.md`` sibling ``mnemo rewrites`` reviews. The
routing and the ledger live in :mod:`mnemo.core.share.imports`; this module
prints one line per decision and a summary.

Exit codes follow ``backfill``'s split between the tree's failures and the
run's: a refused rule (a slug collision, a file that is not ours to
overwrite) and a failed write are reported and counted, the run continues,
and the exit is 1 so a script notices; a stray non-page in the tree is a
counted line and exit 0 — a README beside the rules is not a failure. A
missing or unusable ``PATH`` is a usage error, exit 2.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from mnemo.cli.parser import command

_EXIT_OK = 0
_EXIT_SOME_FAILED = 1
_EXIT_USAGE = 2


def _rel(path: Path, vault: Path) -> str:
    try:
        return path.relative_to(vault).as_posix()
    except ValueError:
        return str(path)


def _default_share_root(cwd: str) -> Path:
    from mnemo.core.agent import resolve_agent
    from mnemo.core.share.format import SHARE_DIR

    # The tree the user is standing in, like `export` — a worktree has its
    # own checkout of the published tree.
    return Path(resolve_agent(cwd).repo_root) / SHARE_DIR


@command("import")
def cmd_import(args: argparse.Namespace) -> int:
    from mnemo import cli  # late binding, as every other command does
    from mnemo.core.agent import resolve_canonical_agent
    from mnemo.core.share import imports as imports_mod

    vault = cli._resolve_vault()
    cwd = os.getcwd()
    raw = getattr(args, "path", None)
    share_root = Path(raw).expanduser() if raw else _default_share_root(cwd)
    if not share_root.is_dir():
        if raw:
            print(f"error: {share_root} is not a directory", file=sys.stderr)
        else:
            print(
                f"error: nothing published in this repo ({share_root.name}/ is missing) — "
                "run `mnemo publish` in the clone that has the rules, or pass a path",
                file=sys.stderr,
            )
        return _EXIT_USAGE

    # The LOCAL canonical name: the publisher's clone may be named
    # differently, and its name is provenance, not authority.
    project = resolve_canonical_agent(cwd).name
    dry_run = bool(getattr(args, "dry_run", False))

    report = imports_mod.run_import(
        vault, share_root=share_root, project=project, dry_run=dry_run,
        today=date.today().isoformat(),
    )

    verb = "would stage" if dry_run else "staged"
    for d in report.decisions:
        if d.action == "staged":
            print(f"{verb} {d.page_type}/{d.slug} → {_rel(d.target, vault)}")
        elif d.action == "proposed":
            print(
                f"{'would propose' if dry_run else 'proposed'} {d.page_type}/{d.slug} → "
                f"{_rel(d.target, vault)} (live page exists; review with `mnemo rewrites`)"
            )
        elif d.action == "refused":
            print(f"refused {d.page_type}/{d.slug}: {d.reason}", file=sys.stderr)
        elif d.action == "failed":
            print(f"failed {d.page_type}/{d.slug}: {d.reason}", file=sys.stderr)
        elif d.action == "not-a-page":
            print(f"skipped {d.source}: {d.reason}", file=sys.stderr)
        elif dry_run:
            # A real run keeps the quiet outcomes to the summary line; a dry
            # run is asked to show every decision.
            print(f"skip {d.page_type}/{d.slug}: {d.reason}")

    parts = [
        f"{report.staged} staged",
        f"{report.proposed} proposed",
        f"{report.unchanged} unchanged",
        f"{report.yours} yours",
        f"{report.refused} refused",
        f"{report.not_pages} not a page",
    ]
    if report.failed:
        parts.append(f"{report.failed} failed")
    head = "import (dry run)" if dry_run else "import"
    print(f"{head}: {', '.join(parts)} from {share_root}")
    if report.wrote and not dry_run:
        print("          review shared/_inbox/ — move keepers to shared/<same type>/, "
              "`mnemo rewrites` for the proposed ones")
    return _EXIT_SOME_FAILED if (report.refused or report.failed) else _EXIT_OK
