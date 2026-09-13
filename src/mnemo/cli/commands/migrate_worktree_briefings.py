"""``mnemo migrate-worktree-briefings`` — relocate orphan worktree briefings.

When the canonical-agent change shipped (v0.10), pre-existing briefings
written under ``bots/<worktree-name>/briefings/sessions/`` are no longer
discoverable by the new SessionStart injection (which reads the
canonical agent dir). This one-shot command finds those orphan dirs and
moves their contents into the canonical agent's dir.

Moving the file is only half the repair (#228). Rule frontmatter, evidence
quotes, wikilinks and the extraction state all cite briefings *by path*, and
both derived indexes read a rule's project out of that path — so a move that
updates nothing else trades a missing briefing for a vault full of dangling
citations and rules filed under a worktree name nothing queries. Every
citation is rewritten in the same run and the indexes rebuilt; see
:mod:`mnemo.core.migrations.briefing_citations`. ``--dry-run`` reports that
blast radius before anything moves.

Heuristic caveat: orphan detection uses a name-prefix match
(``bots/<canonical>-<suffix>/``). If you have an unrelated project
whose name happens to start with the canonical prefix (e.g.,
``myproj-experimental`` next to ``myproj``), it will be swept too.
Always run with ``--dry-run`` first and audit the output before
executing the real move.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from mnemo.cli.parser import command
from mnemo.core import agent as agent_mod
from mnemo.core.migrations import briefing_citations as citations


@command("migrate-worktree-briefings")
def cmd_migrate_worktree_briefings(args: argparse.Namespace) -> int:
    """Move orphan worktree briefings, carrying every citation of them along."""
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

    # Hoisted: ensure each unique target parent exists once, not per file.
    if moves and not dry_run:
        unique_targets = {target.parent for _src, target in moves}
        for t in unique_targets:
            t.mkdir(parents=True, exist_ok=True)

    for src, target in moves:
        if dry_run:
            print(f"would move {src} -> {target}")
        else:
            shutil.move(str(src), str(target))
            print(f"moved {src.name} -> {target.parent}")

    for src in collisions:
        print(f"collision: skipped {src} (target with same name already exists)")

    # The move is the easy half (#228). Everything that cites a briefing by
    # path — rule frontmatter, evidence quotes, wikilinks, the extraction
    # state — still points at where the file used to be, and both derived
    # indexes read the project name out of that path. Repair them in the same
    # breath, and on --dry-run at least report the blast radius so the choice
    # is informed before anything moves.
    path_map = citations.build_path_map(moves, vault)
    plan = citations.plan_citations(vault, path_map)

    if plan.is_empty():
        if moves and not dry_run:
            _rebuild(vault)
        return 0

    if dry_run:
        print(
            f"would rewrite {plan.citation_count} citation(s) "
            f"across {len(plan.pages)} rule page(s):"
        )
        for page in sorted(plan.pages):
            try:
                rel = page.relative_to(vault)
            except ValueError:
                rel = page
            print(f"  {rel} ({plan.pages[page]})")
        if plan.state_keys:
            print(f"would repoint {len(plan.state_keys)} extraction-state entr(ies)")
        print("would rebuild the rule-activation and reflex indexes")
        return 0

    rewritten = citations.apply_citations(vault, plan)
    print(f"rewrote {plan.citation_count} citation(s) in {rewritten} rule page(s)")
    if plan.state_keys:
        print(f"repointed {len(plan.state_keys)} extraction-state entr(ies)")
    _rebuild(vault)
    return 0


def _rebuild(vault: Path) -> None:
    """Rebuild the project-derived indexes, reporting what succeeded."""
    rebuilt = citations.rebuild_indexes(vault)
    if rebuilt:
        print(f"rebuilt index(es): {', '.join(rebuilt)}")
    else:
        print("index rebuild failed — run `mnemo extract` to rebuild (see error log)")
