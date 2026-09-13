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

# Verbatim ``claude --bg`` stdout, captured from a live spawn on 2026-09-13
# with stdout on a **pipe** — the escapes below are really emitted there, not
# an artefact of a terminal.
#
# Two things a hand-written fixture gets wrong, and both are #211's actual
# lesson. Five lines, not one: the blob's last token is the word ``session``,
# which is what the original ``split()[-1]`` returned and printed as the
# attach hint. And the id's first occurrence is wrapped in SGR color, making
# ``\x1b[36m...\x1b[39m`` a single token that matches no id shape at all — so
# a parse that does not strip escapes survives only on the hint lines
# happening to be unstyled.
#
# Kept byte-for-byte because a fixture that cannot fail is what let #211 ship
# green through 2996 passing tests.
REAL_BG_STDOUT = (
    "backgrounded · \x1b[36ma1b2c3d4\x1b[39m\n"
    "\x1b[2m  claude agents             list sessions\x1b[22m\n"
    "\x1b[2m  claude attach a1b2c3d4    open in this terminal\x1b[22m\n"
    "\x1b[2m  claude logs a1b2c3d4      show recent output\x1b[22m\n"
    "\x1b[2m  claude stop a1b2c3d4      stop this session\x1b[22m\n"
)

# The same block with **every** occurrence colored, including the hints.
# Nothing emits this today; it is the one styling change that would silently
# break a parse relying on an unstyled hint line, which is exactly how #211
# broke in the first place.
FULLY_STYLED_BG_STDOUT = (
    "backgrounded · \x1b[36ma1b2c3d4\x1b[39m\n"
    "\x1b[2m  claude attach \x1b[36ma1b2c3d4\x1b[39m    open in this terminal\x1b[22m\n"
)


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
        return subprocess.CompletedProcess(args, 0, stdout=REAL_BG_STDOUT, stderr="")

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
        return subprocess.CompletedProcess(args, 0, stdout=REAL_BG_STDOUT, stderr="")

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
        return subprocess.CompletedProcess(args, 0, stdout=REAL_BG_STDOUT, stderr="")

    tree = dispatch.ensure_worktree(197, repo_root=repo)
    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)

    dispatch.spawn_child("x", cwd=tree)

    assert "timeout" not in seen["args"]


@pytest.mark.real_spawn
def test_spawn_returns_the_short_id(repo: Path, monkeypatch) -> None:
    """The id comes out of the real five-line help block, not a bare line.

    This is #211: the whole blob's last token is the word ``session``, so the
    attach hint read ``claude attach session`` for both children of the first
    real contract dispatch.
    """
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=REAL_BG_STDOUT, stderr="")

    tree = dispatch.ensure_worktree(197, repo_root=repo)
    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)

    assert dispatch.spawn_child("x", cwd=tree) == "a1b2c3d4"


@pytest.mark.real_spawn
@pytest.mark.parametrize(
    "stdout",
    [
        pytest.param(REAL_BG_STDOUT, id="real-help-block"),
        pytest.param(FULLY_STYLED_BG_STDOUT, id="every-occurrence-colored"),
        pytest.param("  a1b2c3d4  \n", id="bare-id"),
        pytest.param(
            "warning: config is stale\n" + REAL_BG_STDOUT, id="noise-before"
        ),
        pytest.param(REAL_BG_STDOUT + "\nall set\n", id="noise-after"),
    ],
)
def test_spawn_finds_the_id_whatever_surrounds_it(
    repo: Path, monkeypatch, stdout: str
) -> None:
    """Position is not load-bearing; the id's *shape* is.

    Anchoring on a line number is what broke here once already. A warning on
    stdout would move the block down, and reading "the last token of the
    first line" would then return ``stale`` — a non-id printed as an attach
    hint, which is #211 again under a different cause. The id is the only
    8-hex token in the block, so that is what is matched.
    """
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    tree = dispatch.ensure_worktree(197, repo_root=repo)
    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)

    assert dispatch.spawn_child("x", cwd=tree) == "a1b2c3d4"


@pytest.mark.real_spawn
def test_spawn_never_truncates_a_longer_hex_run_into_an_id(
    repo: Path, monkeypatch
) -> None:
    """A commit sha is not a short id, and must not be cut down into one.

    The prompt is echoed in some output, so a 40-char sha can precede the
    block. Matched without anchors, its first eight characters are hex and
    would be returned as ``3a14bdd9`` — a well-formed id that addresses no
    session, which is the #211 failure exactly: a plausible token printed as
    an attach hint. The token must be eight hex digits *entire*.
    """
    sha = "3a14bdd9ff01c2b4e5d6a7b8c9d0e1f2a3b4c5d6"

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args, 0, stdout=f"resuming {sha}\n{REAL_BG_STDOUT}", stderr=""
        )

    tree = dispatch.ensure_worktree(197, repo_root=repo)
    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)

    assert dispatch.spawn_child("x", cwd=tree) == "a1b2c3d4"


@pytest.mark.real_spawn
@pytest.mark.parametrize(
    "stdout",
    [
        pytest.param("", id="empty"),
        pytest.param("Started your session in the background.\n", id="prose-only"),
    ],
)
def test_spawn_returns_empty_when_no_id_is_printed(
    repo: Path, monkeypatch, stdout: str
) -> None:
    """No id beats a wrong id.

    ``spawn_child`` returning ``""`` makes the report print a blank column,
    which reads as missing. Returning ``background.`` reads as an id and
    sends the maintainer to a command that cannot work — the failure #211
    was actually made of.
    """
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    tree = dispatch.ensure_worktree(197, repo_root=repo)
    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)

    assert dispatch.spawn_child("x", cwd=tree) == ""


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


# --- dispatching a whole contract ------------------------------------------


def _contract(tmp_path: Path, verdict: str = "parallel"):
    from mnemo.core import contracts

    return contracts.Contract(
        feature="demo",
        verdict=verdict,
        pieces=[
            contracts.Piece(slug="one", files=["a.py"], exposes=["`f()`"]),
            contracts.Piece(slug="two", files=["b.py"], exposes=["`g()`"]),
        ],
        path=tmp_path / "contract.md",
    )


def _boom(prompt, *, cwd):
    raise dispatch.DispatchError("boom")


def test_dispatch_contract_spawns_one_child_per_piece(repo: Path, monkeypatch) -> None:
    spawned: list[Path] = []

    def fake_spawn(prompt, *, cwd):
        spawned.append(cwd)
        return "id1"

    monkeypatch.setattr(dispatch, "spawn_child", fake_spawn)
    results = dispatch.dispatch_contract(_contract(repo), repo_root=repo)
    assert [r.issue for r in results] == ["c-one", "c-two"]
    assert [p.name for p in spawned] == ["proj-wt-c-one", "proj-wt-c-two"]


def test_dispatch_contract_refuses_a_sequential_verdict(repo: Path, monkeypatch) -> None:
    """``sequential`` means the work does not divide — spawning would be wrong."""
    monkeypatch.setattr(dispatch, "spawn_child", lambda *a, **k: pytest.fail("spawned"))
    with pytest.raises(dispatch.DispatchError, match="sequential"):
        dispatch.dispatch_contract(_contract(repo, verdict="sequential"), repo_root=repo)


def test_one_failing_piece_does_not_strand_the_others(repo: Path, monkeypatch) -> None:
    calls = {"n": 0}

    def flaky(prompt, *, cwd):
        calls["n"] += 1
        if calls["n"] == 1:
            raise dispatch.DispatchError("boom")
        return "id2"

    monkeypatch.setattr(dispatch, "spawn_child", flaky)
    results = dispatch.dispatch_contract(_contract(repo), repo_root=repo)
    assert results[0].error == "boom"
    assert results[1].short_id == "id2"
    assert not (repo.parent / "proj-wt-c-one").exists()  # rolled back


def _branches(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def test_rollback_deletes_the_branch_so_the_retry_can_run(repo: Path, monkeypatch) -> None:
    """Removing the tree alone is not a rollback.

    The branch outlives it, and the retry then dies on ``a branch named ...
    already exists`` — a *different* failure from the one rolled back, and one
    no amount of retrying clears.
    """
    monkeypatch.setattr(dispatch, "spawn_child", _boom)

    results = dispatch.dispatch_contract(_contract(repo), repo_root=repo)
    assert [r.error for r in results] == ["boom", "boom"]
    assert "feat/demo/one" not in _branches(repo)
    assert "feat/demo/two" not in _branches(repo)

    # The real proof: the same contract dispatches cleanly afterwards.
    monkeypatch.setattr(dispatch, "spawn_child", lambda prompt, *, cwd: "ok1")
    retry = dispatch.dispatch_contract(_contract(repo), repo_root=repo)
    assert [r.error for r in retry] == [None, None]
    assert {"feat/demo/one", "feat/demo/two"} <= _branches(repo)


def test_an_issue_rollback_also_deletes_its_branch(repo: Path, monkeypatch) -> None:
    """The same guarantee on the issue path, which had the same leak."""
    monkeypatch.setattr(dispatch, "spawn_child", _boom)

    with pytest.raises(dispatch.DispatchError):
        dispatch.dispatch_issue(197, repo_root=repo, fetch=_fake_fetch)

    assert "fix/issue-197" not in _branches(repo)


def test_rollback_never_deletes_a_branch_it_did_not_create(repo: Path) -> None:
    """``worktree add`` most often refuses *because* the branch already exists.

    That branch is someone else's. The rollback for it must remove the
    half-made directory and nothing more.
    """
    subprocess.run(["git", "branch", "fix/issue-197"], cwd=repo, check=True,
                   capture_output=True, text=True)

    with pytest.raises(dispatch.DispatchError):
        dispatch.ensure_worktree(197, repo_root=repo)

    assert "fix/issue-197" in _branches(repo)


def test_a_piece_prompt_reaches_its_child(repo: Path, monkeypatch) -> None:
    """The child must receive its own boundary, not another piece's."""
    seen: dict[str, str] = {}

    def fake_spawn(prompt, *, cwd):
        seen[cwd.name] = prompt
        return "id"

    monkeypatch.setattr(dispatch, "spawn_child", fake_spawn)
    dispatch.dispatch_contract(_contract(repo), repo_root=repo)
    assert "a.py" in seen["proj-wt-c-one"]
    assert "a.py" not in seen["proj-wt-c-two"]
