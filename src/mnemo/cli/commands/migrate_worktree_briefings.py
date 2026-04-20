"""``mnemo migrate-worktree-briefings`` — relocate orphan worktree briefings.

When the canonical-agent change shipped (v0.10), pre-existing briefings
written under ``bots/<worktree-name>/briefings/sessions/`` are no longer
discoverable by the new SessionStart injection (which reads the
canonical agent dir). This one-shot command finds those orphan dirs and
moves their contents into the canonical agent's dir.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from mnemo.cli.parser import command
from mnemo.core import agent as agent_mod


@command("migrate-worktree-briefings")
def cmd_migrate_worktree_briefings(args: argparse.Namespace) -> int:
    """Move orphan worktree briefings into the canonical agent's pool."""
    from mnemo import cli
    vault = cli._resolve_vault()

    repos: list[str] = list(getattr(args, "repos", []) or [])
    dry_run: bool = bool(getattr(args, "dry_run", False))

    if not repos:
        print("(usage) mnemo migrate-worktree-briefings --repos /path/to/repo [/path/to/another ...]")
        return 0

    moves: list[tuple[Path, Path]] = []
    collisions: list[Path] = []

    bots_root = vault / "bots"
    if not bots_root.is_dir():
        print("nothing to migrate (no bots/ dir in vault)")
        return 0

    for repo_path in repos:
        repo_p = Path(repo_path)
        canonical = agent_mod.resolve_canonical_agent(str(repo_p)).name
        # Find any agent dir whose name resolves to the same canonical (i.e. its own
        # .git is a worktree pointing to this repo's main .git).
        for agent_dir in sorted(bots_root.iterdir()):
            if not agent_dir.is_dir() or agent_dir.name == canonical:
                continue
            sessions = agent_dir / "briefings" / "sessions"
            if not sessions.is_dir():
                continue
            # Heuristic: name-prefix match. We can't always cwd-resolve a vault dir
            # back to a worktree because the worktree may have been deleted.
            if not agent_dir.name.startswith(canonical + "-"):
                continue
            target_dir = bots_root / canonical / "briefings" / "sessions"
            for src in sorted(sessions.glob("*.md")):
                target = target_dir / src.name
                if target.exists():
                    collisions.append(src)
                else:
                    moves.append((src, target))

    if not moves and not collisions:
        print("nothing to migrate")
        return 0

    for src, target in moves:
        if dry_run:
            print(f"would move {src} -> {target}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(target))
            print(f"moved {src.name} -> {target.parent}")

    for src in collisions:
        print(f"collision: skipped {src} (target with same name already exists)")

    return 0
