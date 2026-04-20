"""Worktree-aware canonical agent resolution."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from mnemo.core import agent


def test_canonical_agent_main_repo(tmp_path: Path) -> None:
    """A normal repo (.git is a directory) resolves to its own basename."""
    repo = tmp_path / "myproject"
    repo.mkdir()
    (repo / ".git").mkdir()
    info = agent.resolve_canonical_agent(str(repo))
    assert info.name == "myproject"
    assert info.repo_root == str(repo.resolve())
    assert info.has_git is True


def test_canonical_agent_worktree_resolves_to_main(tmp_path: Path) -> None:
    """A worktree (.git is a file with `gitdir:` pointer) resolves to the main repo's basename."""
    main_repo = tmp_path / "myproject"
    main_repo.mkdir()
    git_dir = main_repo / ".git"
    git_dir.mkdir()
    worktrees_dir = git_dir / "worktrees" / "feature-x"
    worktrees_dir.mkdir(parents=True)
    (worktrees_dir / "commondir").write_text("../..\n")  # points back to git_dir

    worktree = tmp_path / "myproject-feature-x"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {worktrees_dir}\n")

    info = agent.resolve_canonical_agent(str(worktree))
    assert info.name == "myproject"
    assert info.repo_root == str(main_repo.resolve())


def test_canonical_agent_no_git_falls_back(tmp_path: Path) -> None:
    """When no .git is found, falls back to resolve_agent (basename of cwd)."""
    plain = tmp_path / "plainfolder"
    plain.mkdir()
    info = agent.resolve_canonical_agent(str(plain))
    assert info.name == "plainfolder"
    assert info.has_git is False


def test_canonical_agent_malformed_git_file_falls_back(tmp_path: Path) -> None:
    """A `.git` file with no parseable `gitdir:` line degrades to current basename."""
    repo = tmp_path / "weird"
    repo.mkdir()
    (repo / ".git").write_text("not a real gitdir pointer\n")
    info = agent.resolve_canonical_agent(str(repo))
    assert info.name == "weird"
    assert info.has_git is True


def test_canonical_agent_missing_commondir_falls_back(tmp_path: Path) -> None:
    """When .git points to a worktree dir without `commondir`, fall back to current basename."""
    repo = tmp_path / "broken"
    repo.mkdir()
    fake_target = tmp_path / "fake-gitdir"
    fake_target.mkdir()
    (repo / ".git").write_text(f"gitdir: {fake_target}\n")
    info = agent.resolve_canonical_agent(str(repo))
    assert info.name == "broken"
