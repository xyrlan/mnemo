"""``mnemo dispatch <issue>...`` — one background child per issue, one tree each.

Human-only, like ``mnemo sessions``: no hook and no MCP tool exposes it.
Spawning work is a decision, and a decision belongs to the maintainer.

This command starts work and stops there. A blocked child *can* be answered
programmatically — ``--resume`` bifurcates, but ``SendMessage`` reaches one —
and it deliberately does not: a child blocks exactly when it wants human
judgement, and an invented answer is the #187 failure again. So the closing
hint points the maintainer at the queue, ``mnemo sessions``, which is
pipe-safe, rather than at ``claude agents``, which requires a TTY.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from mnemo.cli.parser import command


def _repo_root() -> Path | None:
    """The git toplevel of the cwd, or ``None`` when there is not one."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    return Path(top) if top else None


@command("dispatch")
def cmd_dispatch(args: argparse.Namespace) -> int:
    """Spawn a child per issue. Returns 1 if any of them failed to start."""
    from mnemo.core import dispatch as core

    root = _repo_root()
    if root is None:
        print("not inside a git repository — dispatch needs one to branch from")
        return 1

    issues = list(getattr(args, "issues", []) or [])

    if getattr(args, "dry_run", False):
        # Printable without side effects: the paths and branches are pure
        # functions of the issue numbers, so the plan can be checked first.
        for issue in issues:
            tree = core.worktree_path(issue, repo_root=root)
            print(f"#{issue}  {core.branch_name(issue)}  {tree}")
        return 0

    results = core.dispatch_all(issues, repo_root=root)

    started = [r for r in results if r.error is None]
    failed = [r for r in results if r.error is not None]

    for r in started:
        print(f"#{r.issue}  {r.short_id}  {r.worktree}")
    for r in failed:
        # Never silent: a skipped issue the maintainer does not see is one
        # they will assume is running.
        print(f"#{r.issue}  FAILED: {r.error}")

    if started:
        print()
        print(f"  queue:  mnemo sessions")
        print(f"  attach: claude attach {started[0].short_id}")

    return 1 if failed else 0
