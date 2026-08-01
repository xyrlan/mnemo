"""Tests for the worktree-based self-fix git plumbing.

The self-fix subsystem must never mutate the checkout a human is using:
no ``git checkout``, no HEAD movement, no index writes in the live repo.
Every PR is built inside a throwaway ``git worktree``.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mnemo.autopilot.selffix import _gh


# ---------------------------------------------------------------------------
# Fixtures — real git repos (this plumbing is only meaningful against real git)
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo with one commit on ``master`` and a ``shared/`` file."""
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-b", "master", cwd=root)
    _git("config", "user.email", "test@example.com", cwd=root)
    _git("config", "user.name", "Test", cwd=root)
    shared = root / "shared" / "feedback"
    shared.mkdir(parents=True)
    (shared / "keep.md").write_text("original\n", encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git("commit", "-m", "init", cwd=root)
    return root


def _head_ref(root: Path) -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD", cwd=root)


# ---------------------------------------------------------------------------
# resolve_repo_root
# ---------------------------------------------------------------------------


def test_resolve_repo_root_returns_toplevel_for_a_repo(repo: Path) -> None:
    nested = repo / "shared" / "feedback"
    assert _gh.resolve_repo_root(nested) == repo.resolve()


def test_resolve_repo_root_returns_none_outside_a_repo(tmp_path: Path) -> None:
    lonely = tmp_path / "not-a-repo"
    lonely.mkdir()
    assert _gh.resolve_repo_root(lonely) is None


def test_resolve_repo_root_returns_none_for_missing_dir(tmp_path: Path) -> None:
    assert _gh.resolve_repo_root(tmp_path / "gone") is None


# ---------------------------------------------------------------------------
# create_worktree
# ---------------------------------------------------------------------------


def test_create_worktree_leaves_live_head_untouched(repo: Path) -> None:
    before_ref = _head_ref(repo)
    before_sha = _git("rev-parse", "HEAD", cwd=repo)

    wt = _gh.create_worktree("mnemo/self-fix/doctor-2026-08-01", repo_root=repo)

    assert wt is not None
    assert wt.is_dir()
    assert _head_ref(repo) == before_ref
    assert _git("rev-parse", "HEAD", cwd=repo) == before_sha
    assert _head_ref(wt) == "mnemo/self-fix/doctor-2026-08-01"

    _gh.remove_worktree(wt, repo_root=repo)


def test_create_worktree_lives_outside_the_repo(repo: Path) -> None:
    wt = _gh.create_worktree("mnemo/self-fix/x", repo_root=repo)
    assert wt is not None
    with pytest.raises(ValueError):
        wt.resolve().relative_to(repo.resolve())
    _gh.remove_worktree(wt, repo_root=repo)


def test_create_worktree_returns_none_when_branch_exists(repo: Path) -> None:
    _git("branch", "mnemo/self-fix/dup", cwd=repo)
    assert _gh.create_worktree("mnemo/self-fix/dup", repo_root=repo) is None
    assert _head_ref(repo) == "master"


def test_create_worktree_returns_none_when_git_missing(repo: Path, monkeypatch) -> None:
    def boom(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", boom)
    assert _gh.create_worktree("mnemo/self-fix/x", repo_root=repo) is None


# ---------------------------------------------------------------------------
# mirror_paths
# ---------------------------------------------------------------------------


def test_mirror_paths_copies_modified_files_into_worktree(repo: Path) -> None:
    wt = _gh.create_worktree("mnemo/self-fix/copy", repo_root=repo)
    assert wt is not None
    live = repo / "shared" / "feedback" / "keep.md"
    live.write_text("healed\n", encoding="utf-8")

    _gh.mirror_paths([live], source_root=repo, worktree=wt)

    assert (wt / "shared" / "feedback" / "keep.md").read_text(encoding="utf-8") == "healed\n"
    _gh.remove_worktree(wt, repo_root=repo)


def test_mirror_paths_creates_parent_dirs_for_new_files(repo: Path) -> None:
    wt = _gh.create_worktree("mnemo/self-fix/new", repo_root=repo)
    assert wt is not None
    new = repo / "shared" / "_archive" / "dead.md"
    new.parent.mkdir(parents=True)
    new.write_text("archived\n", encoding="utf-8")

    _gh.mirror_paths([new], source_root=repo, worktree=wt)

    assert (wt / "shared" / "_archive" / "dead.md").read_text(encoding="utf-8") == "archived\n"
    _gh.remove_worktree(wt, repo_root=repo)


def test_mirror_paths_deletes_paths_gone_from_the_live_tree(repo: Path) -> None:
    """An archived rule vanishes from its old path — the worktree must follow."""
    wt = _gh.create_worktree("mnemo/self-fix/del", repo_root=repo)
    assert wt is not None
    live = repo / "shared" / "feedback" / "keep.md"
    live.unlink()

    _gh.mirror_paths([live], source_root=repo, worktree=wt)

    assert not (wt / "shared" / "feedback" / "keep.md").exists()
    _gh.remove_worktree(wt, repo_root=repo)


def test_mirror_paths_ignores_paths_outside_the_source_root(repo: Path, tmp_path: Path) -> None:
    wt = _gh.create_worktree("mnemo/self-fix/outside", repo_root=repo)
    assert wt is not None
    stray = tmp_path / "stray.md"
    stray.write_text("nope\n", encoding="utf-8")

    _gh.mirror_paths([stray], source_root=repo, worktree=wt)

    assert not (wt / "stray.md").exists()
    _gh.remove_worktree(wt, repo_root=repo)


# ---------------------------------------------------------------------------
# commit_all
# ---------------------------------------------------------------------------


def test_commit_all_commits_mirrored_changes_on_the_worktree_branch(repo: Path) -> None:
    wt = _gh.create_worktree("mnemo/self-fix/commit", repo_root=repo)
    assert wt is not None
    live = repo / "shared" / "feedback" / "keep.md"
    live.write_text("healed\n", encoding="utf-8")
    _gh.mirror_paths([live], source_root=repo, worktree=wt)

    assert _gh.commit_all("fix(autopilot): heal", worktree=wt) is True

    assert _git("log", "-1", "--format=%s", "mnemo/self-fix/commit", cwd=repo) == (
        "fix(autopilot): heal"
    )
    changed = _git("show", "--name-only", "--format=", "mnemo/self-fix/commit", cwd=repo)
    assert "shared/feedback/keep.md" in changed
    _gh.remove_worktree(wt, repo_root=repo)


def test_commit_all_returns_false_when_nothing_changed(repo: Path) -> None:
    wt = _gh.create_worktree("mnemo/self-fix/empty", repo_root=repo)
    assert wt is not None

    assert _gh.commit_all("fix(autopilot): nothing", worktree=wt) is False

    _gh.remove_worktree(wt, repo_root=repo)


def test_commit_all_does_not_touch_the_live_index(repo: Path) -> None:
    """A dirty live working tree must survive the self-fix run untouched."""
    dirty = repo / "shared" / "feedback" / "wip.md"
    dirty.write_text("uncommitted human work\n", encoding="utf-8")
    wt = _gh.create_worktree("mnemo/self-fix/dirty", repo_root=repo)
    assert wt is not None
    live = repo / "shared" / "feedback" / "keep.md"
    live.write_text("healed\n", encoding="utf-8")
    _gh.mirror_paths([live], source_root=repo, worktree=wt)
    _gh.commit_all("fix(autopilot): heal", worktree=wt)

    assert dirty.read_text(encoding="utf-8") == "uncommitted human work\n"
    status = _git("status", "--porcelain", cwd=repo)
    assert "?? shared/feedback/wip.md" in status
    assert _head_ref(repo) == "master"
    _gh.remove_worktree(wt, repo_root=repo)


# ---------------------------------------------------------------------------
# remove_worktree
# ---------------------------------------------------------------------------


def test_remove_worktree_deletes_the_dir_and_deregisters_it(repo: Path) -> None:
    wt = _gh.create_worktree("mnemo/self-fix/cleanup", repo_root=repo)
    assert wt is not None

    _gh.remove_worktree(wt, repo_root=repo)

    assert not wt.exists()
    assert str(wt) not in _git("worktree", "list", cwd=repo)


def test_remove_worktree_forces_removal_of_a_dirty_worktree(repo: Path) -> None:
    wt = _gh.create_worktree("mnemo/self-fix/dirtywt", repo_root=repo)
    assert wt is not None
    (wt / "shared" / "feedback" / "keep.md").write_text("uncommitted\n", encoding="utf-8")

    _gh.remove_worktree(wt, repo_root=repo)

    assert not wt.exists()


def test_remove_worktree_is_a_noop_for_a_missing_path(repo: Path, tmp_path: Path) -> None:
    _gh.remove_worktree(tmp_path / "never-existed", repo_root=repo)


# ---------------------------------------------------------------------------
# The bug: no checkout in the live repo, ever
# ---------------------------------------------------------------------------


def test_gh_module_no_longer_exposes_create_branch() -> None:
    """`create_branch` ran `git checkout -b` in the live repo — it is gone."""
    assert not hasattr(_gh, "create_branch")
