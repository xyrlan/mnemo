"""``mnemo dispatch`` — worktree creation, spawn, and what happens when it fails.

Leaving a stray worktree behind is worse than refusing to start: the next
dispatch of the same issue finds the directory occupied and either fails or,
far worse, reuses a tree whose branch points somewhere unrelated.

One worktree per child is mandatory, not advisory. Sharing a tree between
parallel sessions has already cost three git accidents in one turn: a branch
taken from another session's branch, ``add -A`` sweeping another session's
files, and ``--amend`` rewriting another session's commit.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mnemo.core import dispatch


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A real git repo with one commit — worktree behaviour is not mockable."""
    root = tmp_path / "proj"
    root.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=root, check=True,
            capture_output=True, text=True,
            env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                 "PATH": __import__("os").environ.get("PATH", ""), "HOME": str(tmp_path)},
        )

    git("init", "-b", "master")
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    git("add", "README.md")
    git("commit", "-m", "init")
    return root


def _worktrees(repo: Path) -> list[Path]:
    """The worktree paths git knows about, as Paths.

    Returned as ``Path`` rather than ``str`` because on Windows ``git worktree
    list`` prints forward slashes while ``Path`` renders backslashes, so
    comparing the raw strings fails on a path that is in fact the same one.
    """
    out = subprocess.run(
        ["git", "worktree", "list"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout
    return [Path(line.split()[0]).resolve() for line in out.splitlines() if line.strip()]


# --- the happy path --------------------------------------------------------


def test_creates_a_worktree_on_its_own_branch(repo: Path) -> None:
    tree = dispatch.ensure_worktree(197, repo_root=repo)

    assert tree.is_dir()
    assert tree.resolve() in _worktrees(repo)

    head = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=tree,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert head == "fix/issue-197"


def test_each_issue_gets_its_own_tree(repo: Path) -> None:
    a = dispatch.ensure_worktree(197, repo_root=repo)
    b = dispatch.ensure_worktree(198, repo_root=repo)

    assert a != b
    assert {a.resolve(), b.resolve()} <= set(_worktrees(repo))


# --- failure modes ---------------------------------------------------------


def test_refuses_when_the_worktree_already_exists(repo: Path) -> None:
    """A live tree may hold another session's uncommitted work. Never reuse."""
    dispatch.ensure_worktree(197, repo_root=repo)

    with pytest.raises(dispatch.DispatchError, match="already exists"):
        dispatch.ensure_worktree(197, repo_root=repo)


def test_refuses_when_the_path_is_occupied_by_a_stranger(repo: Path) -> None:
    """Not a worktree, just a directory in the way. Still refuse."""
    squatter = dispatch.worktree_path(197, repo_root=repo)
    squatter.mkdir(parents=True)
    (squatter / "someone-elses-work.txt").write_text("x", encoding="utf-8")

    with pytest.raises(dispatch.DispatchError, match="already exists"):
        dispatch.ensure_worktree(197, repo_root=repo)

    assert (squatter / "someone-elses-work.txt").exists()


def test_leaves_no_stray_worktree_when_the_branch_is_taken(repo: Path) -> None:
    """The branch exists but no tree holds it: git refuses, and so must we —
    without leaving a half-made directory for the next run to trip over."""
    subprocess.run(["git", "branch", "fix/issue-197"], cwd=repo, check=True,
                   capture_output=True, text=True)
    before = _worktrees(repo)

    with pytest.raises(dispatch.DispatchError):
        dispatch.ensure_worktree(197, repo_root=repo)

    assert _worktrees(repo) == before
    assert not dispatch.worktree_path(197, repo_root=repo).exists()


def test_removes_the_worktree_when_the_spawn_fails(repo: Path, monkeypatch) -> None:
    """A dispatch that dies between worktree and spawn must clean up after
    itself. A stray tree blocks every later attempt at the same issue."""
    def boom(*a, **k):
        raise dispatch.DispatchError("claude: command not found")

    monkeypatch.setattr(dispatch, "spawn_child", boom)

    with pytest.raises(dispatch.DispatchError):
        dispatch.dispatch_issue(197, repo_root=repo, fetch=_fake_fetch)

    assert not dispatch.worktree_path(197, repo_root=repo).exists()
    assert dispatch.worktree_path(197, repo_root=repo) not in _worktrees(repo)


def test_refuses_an_issue_that_does_not_exist(repo: Path) -> None:
    """Refuse before touching git: no tree, no branch, nothing to undo."""
    def missing(issue: int, *, repo_root):
        raise dispatch.DispatchError(f"issue #{issue} not found")

    before = _worktrees(repo)

    with pytest.raises(dispatch.DispatchError, match="not found"):
        dispatch.dispatch_issue(4242, repo_root=repo, fetch=missing)

    assert _worktrees(repo) == before
    assert not dispatch.worktree_path(4242, repo_root=repo).exists()


# --- the spawn contract ----------------------------------------------------


def _fake_fetch(issue: int, *, repo_root):
    return dispatch.Issue(number=issue, title="t", body="b")


@pytest.mark.real_spawn
def test_spawn_passes_the_prompt_positionally_with_bg(repo: Path, monkeypatch) -> None:
    """``--bg`` and ``-p/--print`` conflict: ``--print`` never starts the
    interactive session ``claude attach`` needs, so the job is unattachable.
    The prompt is positional."""
    seen: dict = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(args, 0, stdout="a1b2c3d4\n", stderr="")

    tree = dispatch.ensure_worktree(197, repo_root=repo)
    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)
    dispatch.spawn_child("do the thing", cwd=tree)

    args = seen["args"]
    assert args[0] == "claude"
    assert "--bg" in args
    assert "-p" not in args and "--print" not in args
    assert args[-1] == "do the thing"  # positional, and last
    assert seen["cwd"] == str(tree)


@pytest.mark.real_spawn
def test_spawn_runs_inside_the_child_worktree(repo: Path, monkeypatch) -> None:
    """The cwd is what later recovers the issue number, so it must be the tree."""
    seen: dict = {}

    def fake_run(args, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(args, 0, stdout="a1b2c3d4\n", stderr="")

    tree = dispatch.ensure_worktree(197, repo_root=repo)
    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)

    dispatch.spawn_child("x", cwd=tree)

    assert dispatch.issue_for_cwd(seen["cwd"]) == 197


@pytest.mark.real_spawn
def test_spawn_is_never_wrapped_in_timeout(repo: Path, monkeypatch) -> None:
    """``timeout`` is not on the macOS PATH."""
    seen: dict = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="a1b2c3d4\n", stderr="")

    tree = dispatch.ensure_worktree(197, repo_root=repo)
    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)

    dispatch.spawn_child("x", cwd=tree)

    assert "timeout" not in seen["args"]


@pytest.mark.real_spawn
def test_spawn_returns_the_short_id(repo: Path, monkeypatch) -> None:
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="  a1b2c3d4  \n", stderr="")

    tree = dispatch.ensure_worktree(197, repo_root=repo)
    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)

    assert dispatch.spawn_child("x", cwd=tree) == "a1b2c3d4"


@pytest.mark.real_spawn
def test_spawn_raises_when_claude_is_missing(repo: Path, monkeypatch) -> None:
    tree = dispatch.ensure_worktree(197, repo_root=repo)

    def fake_run(args, **kwargs):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)

    with pytest.raises(dispatch.DispatchError):
        dispatch.spawn_child("x", cwd=tree)


@pytest.mark.real_spawn
def test_spawn_raises_when_claude_exits_nonzero(repo: Path, monkeypatch) -> None:
    tree = dispatch.ensure_worktree(197, repo_root=repo)

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="--bg cannot be used with --print")

    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)

    with pytest.raises(dispatch.DispatchError, match="--print"):
        dispatch.spawn_child("x", cwd=tree)


# --- partial failure across several issues ---------------------------------


def test_one_bad_issue_does_not_strand_the_others(repo: Path, monkeypatch) -> None:
    """#4242 does not exist; #197 must still run, and #4242 must leave no tree."""
    monkeypatch.setattr(dispatch, "spawn_child", lambda prompt, *, cwd: "aaaa1111")

    def fetch(issue: int, *, repo_root):
        if issue == 4242:
            raise dispatch.DispatchError(f"issue #{issue} not found")
        return dispatch.Issue(number=issue, title="t", body="b")

    results = dispatch.dispatch_all([197, 4242], repo_root=repo, fetch=fetch)

    ok = [r for r in results if r.error is None]
    bad = [r for r in results if r.error is not None]

    assert [r.issue for r in ok] == [197]
    assert [r.issue for r in bad] == [4242]
    assert dispatch.worktree_path(197, repo_root=repo).is_dir()
    assert not dispatch.worktree_path(4242, repo_root=repo).exists()


# --- addressing a child by a contract piece --------------------------------


def test_piece_slug_names_a_worktree(tmp_path: Path) -> None:
    root = tmp_path / "mnemo"
    tree = dispatch.worktree_path("c-parser", repo_root=root)
    assert tree.name == "mnemo-wt-c-parser"
    assert tree.parent == root.parent


def test_issue_number_still_names_a_worktree(tmp_path: Path) -> None:
    root = tmp_path / "mnemo"
    assert dispatch.worktree_path(193, repo_root=root).name == "mnemo-wt-193"


def test_slug_worktree_does_not_nest(tmp_path: Path) -> None:
    """Dispatching from inside a slug-named child must not stack suffixes."""
    root = tmp_path / "mnemo-wt-c-parser"
    tree = dispatch.worktree_path("c-seam", repo_root=root)
    assert tree.name == "mnemo-wt-c-seam"


def test_issue_worktree_still_does_not_nest(tmp_path: Path) -> None:
    root = tmp_path / "mnemo-wt-197"
    assert dispatch.worktree_path(198, repo_root=root).name == "mnemo-wt-198"


def test_issue_for_cwd_reads_a_slug_back(tmp_path: Path) -> None:
    assert dispatch.issue_for_cwd("/x/mnemo-wt-c-parser") == "c-parser"


def test_issue_for_cwd_still_reads_an_int_back(tmp_path: Path) -> None:
    assert dispatch.issue_for_cwd("/x/mnemo-wt-193") == 193


def test_hand_made_worktree_is_still_not_a_dispatch(tmp_path: Path) -> None:
    """The guard the ``\\d+`` anchor existed to provide, preserved.

    A directory someone named by hand must not be reported as a dispatch
    child — a false positive mislabels an unrelated session in the queue.
    """
    assert dispatch.issue_for_cwd("/x/mnemo-wt-feature") is None
    assert dispatch.issue_for_cwd("/x/mnemo-wt-My-Branch") is None


def test_piece_branch_is_namespaced_by_feature() -> None:
    name = dispatch.branch_name("c-parser", feature="contract-dispatch")
    assert name == "feat/contract-dispatch/parser"


def test_issue_branch_is_unchanged() -> None:
    assert dispatch.branch_name(193) == "fix/issue-193"


def test_a_piece_slug_without_its_feature_is_refused() -> None:
    """`fix/issue-c-parser` names an issue that does not exist — fail loudly."""
    with pytest.raises(ValueError, match="feature"):
        dispatch.branch_name("c-parser")
