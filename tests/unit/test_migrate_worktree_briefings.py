"""mnemo migrate-worktree-briefings moves orphan briefings to canonical agent dir."""
from __future__ import annotations

import argparse
from pathlib import Path

from mnemo.cli.commands import migrate_worktree_briefings as cmd_mod


def _make_repo_with_worktree_briefings(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Returns (vault, main_repo, worktree)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    main = tmp_path / "myproj"
    main.mkdir()
    (main / ".git").mkdir()
    wt_gitdir = main / ".git" / "worktrees" / "feature-x"
    wt_gitdir.mkdir(parents=True)
    (wt_gitdir / "commondir").write_text("../..\n")
    worktree = tmp_path / "myproj-feature-x"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_gitdir}\n")

    # Orphan briefing under the worktree's old agent dir.
    orphan_dir = vault / "bots" / "myproj-feature-x" / "briefings" / "sessions"
    orphan_dir.mkdir(parents=True)
    (orphan_dir / "session-1.md").write_text("orphan briefing", encoding="utf-8")

    return vault, main, worktree


def test_migrate_dry_run_lists_moves_only(tmp_path: Path, capsys, monkeypatch) -> None:
    vault, main, _wt = _make_repo_with_worktree_briefings(tmp_path)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = argparse.Namespace(dry_run=True, repos=[str(main)])
    rc = cmd_mod.cmd_migrate_worktree_briefings(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "would move" in out
    assert "session-1.md" in out
    # No move actually happened.
    assert (vault / "bots" / "myproj-feature-x" / "briefings" / "sessions" / "session-1.md").exists()
    assert not (vault / "bots" / "myproj" / "briefings" / "sessions" / "session-1.md").exists()


def test_migrate_moves_orphans(tmp_path: Path, monkeypatch) -> None:
    vault, main, _wt = _make_repo_with_worktree_briefings(tmp_path)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = argparse.Namespace(dry_run=False, repos=[str(main)])
    rc = cmd_mod.cmd_migrate_worktree_briefings(args)
    assert rc == 0
    moved = vault / "bots" / "myproj" / "briefings" / "sessions" / "session-1.md"
    assert moved.exists()
    assert moved.read_text() == "orphan briefing"
    # Source removed.
    assert not (vault / "bots" / "myproj-feature-x" / "briefings" / "sessions" / "session-1.md").exists()


def test_migrate_skips_collisions(tmp_path: Path, capsys, monkeypatch) -> None:
    """If a target file with the same name already exists in canonical dir, skip + warn."""
    vault, main, _wt = _make_repo_with_worktree_briefings(tmp_path)
    canonical_dir = vault / "bots" / "myproj" / "briefings" / "sessions"
    canonical_dir.mkdir(parents=True)
    (canonical_dir / "session-1.md").write_text("existing canonical", encoding="utf-8")

    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = argparse.Namespace(dry_run=False, repos=[str(main)])
    rc = cmd_mod.cmd_migrate_worktree_briefings(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "collision" in out.lower() or "skipped" in out.lower()
    # Original orphan stays in place.
    assert (vault / "bots" / "myproj-feature-x" / "briefings" / "sessions" / "session-1.md").exists()
    # Canonical file untouched.
    assert (canonical_dir / "session-1.md").read_text() == "existing canonical"


def test_migrate_noop_when_nothing_to_move(tmp_path: Path, capsys, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    main = tmp_path / "myproj"
    main.mkdir()
    (main / ".git").mkdir()
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = argparse.Namespace(dry_run=False, repos=[str(main)])
    rc = cmd_mod.cmd_migrate_worktree_briefings(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "nothing to migrate" in out.lower()


def test_migrate_sweeps_name_prefix_siblings_known_limitation(tmp_path: Path, capsys, monkeypatch) -> None:
    """KNOWN LIMITATION: the name-prefix heuristic cannot distinguish an
    unrelated project ``myproj-experimental`` from a worktree ``myproj-feature-x``.
    Both start with ``myproj-``, so both are targets. Document that behavior
    here so any future fix that tightens the heuristic breaks this test
    loudly — prompting a code owner to decide whether the fix is wanted.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    main = tmp_path / "myproj"
    main.mkdir()
    (main / ".git").mkdir()

    # An UNRELATED project whose name shares a prefix with `myproj`.
    # This is NOT a worktree — it has its own .git/ (or no .git, doesn't matter).
    unrelated_dir = vault / "bots" / "myproj-experimental" / "briefings" / "sessions"
    unrelated_dir.mkdir(parents=True)
    (unrelated_dir / "unrelated-1.md").write_text("unrelated briefing", encoding="utf-8")

    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = argparse.Namespace(dry_run=True, repos=[str(main)])
    rc = cmd_mod.cmd_migrate_worktree_briefings(args)
    assert rc == 0
    out = capsys.readouterr().out
    # The heuristic would pick up the unrelated project's briefing.
    assert "unrelated-1.md" in out
