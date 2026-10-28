"""``_git_template``: a copied git fixture is the test's own, not the template's (#799)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.unit import _git_template


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True,
    ).stdout.strip()


def _build(root: Path) -> None:
    """A bare origin, its clone ``proj``, and a worktree ``proj-wt-1`` of it."""
    origin, repo = root / "origin.git", root / "proj"
    _git("init", "--bare", "-b", "master", str(origin), cwd=root)
    _git("clone", "-q", str(origin), str(repo), cwd=root)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
         "--allow-empty", "-m", "base", cwd=repo)
    _git("push", "-q", "-u", "origin", "master", cwd=repo)
    _git("worktree", "add", "-q", "-b", "fix/issue-1", str(root / "proj-wt-1"), cwd=repo)


def _relink(root: Path) -> None:
    _git_template.set_remote_url(root / "proj", "origin", root / "origin.git")
    _git_template.relink_worktree(root / "proj", root / "proj-wt-1")


def _same(a: str, b: Path) -> bool:
    return Path(a).resolve() == b.resolve()


def test_a_copy_left_naming_its_template_is_refused(tmp_path, tmp_path_factory):
    with pytest.raises(AssertionError, match="still names its template"):
        _git_template.copy("git-template-test", _build, tmp_path, tmp_path_factory)


def test_a_relinked_copy_points_only_at_itself(tmp_path, tmp_path_factory):
    root = _git_template.copy("git-template-test", _build, tmp_path, tmp_path_factory,
                              relink=_relink)
    repo, tree = root / "proj", root / "proj-wt-1"

    listed = [line.split(" ", 1)[1] for line in
              _git("worktree", "list", "--porcelain", cwd=repo).splitlines()
              if line.startswith("worktree ")]
    assert [_same(p, q) for p, q in zip(listed, (repo, tree))] == [True, True]
    assert _same(_git("remote", "get-url", "origin", cwd=repo), root / "origin.git")
    assert _same(_git("rev-parse", "--show-toplevel", cwd=tree), tree)
    assert _git("status", "--porcelain", cwd=tree) == ""


def test_copies_do_not_see_each_others_writes(tmp_path, tmp_path_factory):
    a = _git_template.copy("git-template-test", _build, tmp_path / "a", tmp_path_factory,
                           relink=_relink)
    b = _git_template.copy("git-template-test", _build, tmp_path / "b", tmp_path_factory,
                           relink=_relink)
    template = _git_template._BUILT["git-template-test"]

    _git("worktree", "remove", str(a / "proj-wt-1"), cwd=a / "proj")
    _git("push", "-q", "origin", "HEAD:refs/heads/only-in-a", cwd=a / "proj")

    assert not (a / "proj-wt-1").exists()
    assert _git("branch", "--list", "only-in-a", cwd=a / "origin.git") != ""
    assert _git("branch", "--list", "only-in-a", cwd=b / "origin.git") == ""
    assert _git("branch", "--list", "only-in-a", cwd=template / "origin.git") == ""
    assert (b / "proj-wt-1" / ".git").is_file()
    assert (template / "proj-wt-1" / ".git").is_file()
    assert "proj-wt-1" in _git("worktree", "list", cwd=b / "proj")
