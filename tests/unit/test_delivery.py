"""The last metre: is a child's work deliverable, and was a PR already opened?

Readiness is asserted against **real git repositories**, not mocked
``subprocess`` calls. The whole claim of :mod:`mnemo.core.sessions.delivery`
is that git is authoritative where session state is not, and a test that
stubs git tests the stub. Four bugs shipped green on 2026-09-13 behind
fixtures whose shape was not production's, so the ones that cannot be built
from real git — ``gh`` output — are captured verbatim from a live call.

``gh`` itself is never invoked: the network is not a unit test's to depend on,
and a repository with no GitHub remote is the normal case in CI.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mnemo.core.sessions import delivery

# Verbatim `gh pr list --head <branch> --state all --json number,url --limit 1`
# stdout, captured 2026-09-13 against this repo's own merged #214. Two shapes,
# because the empty one is what every not-yet-delivered branch returns and it
# is the branch of every case this command exists for.
REAL_GH_PR = '[{"number":214,"url":"https://github.com/xyrlan/mnemo/pull/214"}]\n'
REAL_GH_NONE = "[]\n"

# The head of the body `--fill` actually wrote on PR #223, captured verbatim
# with `gh pr view 223 --json body`. The case of #224: prose the child wrote,
# naming its issue as `(#222)` in the commit subject — a reference GitHub does
# not act on — and carrying no closing keyword anywhere.
REAL_FILLED_BODY = (
    "`Session` answers the question consumers actually ask — is this waiting "
    "on\nme, is it finished — correctly and in one place."
)


def _run(args, *, cwd) -> None:
    subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo with one commit on ``master``, and no remote.

    ``-b master`` explicitly: the module measures against ``master`` by name
    and a git whose ``init.defaultBranch`` is ``main`` would otherwise make
    every test here pass or fail for a reason that is not the code's.
    """
    root = tmp_path / "mnemo"
    root.mkdir()
    _run(["git", "init", "-b", "master"], cwd=root)
    _run(["git", "config", "user.email", "t@example.com"], cwd=root)
    _run(["git", "config", "user.name", "t"], cwd=root)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _run(["git", "add", "README.md"], cwd=root)
    _run(["git", "commit", "-m", "base"], cwd=root)
    return root


def _worktree(repo: Path, target: str, branch: str) -> Path:
    """A dispatch worktree named the way :mod:`mnemo.core.dispatch` names one."""
    tree = repo.parent / f"mnemo-wt-{target}"
    _run(["git", "worktree", "add", "-b", branch, str(tree)], cwd=repo)
    return tree


def _commit(tree: Path, name: str = "work.py", body: str = "x = 1\n") -> None:
    (tree / name).write_text(body, encoding="utf-8")
    _run(["git", "add", name], cwd=tree)
    _run(["git", "commit", "-m", f"add {name}"], cwd=tree)


@pytest.fixture(autouse=True)
def no_gh(monkeypatch: pytest.MonkeyPatch, request):
    """No test here reaches the network.

    Stubs the ``gh`` *subprocess*, not :func:`delivery.pr_for` — stubbing the
    function would shadow the tests below that exercise its own parsing, and
    they would pass against the stub rather than against the code.
    """
    if request.node.get_closest_marker("real_subprocess"):
        return
    real = subprocess.run

    def fake_run(args, **kw):
        if args and args[0] == "gh":
            return subprocess.CompletedProcess(args, 0, "[]", "")
        return real(args, **kw)

    monkeypatch.setattr(subprocess, "run", fake_run)


# --- readiness: from git, never from session state (#217) ------------------


def test_a_clean_branch_ahead_of_master_is_ready(repo: Path) -> None:
    tree = _worktree(repo, "c-delivery", "feat/f/delivery")
    _commit(tree)

    state = delivery.ready(tree, repo_root=repo)

    assert state.ready
    assert state.branch == "feat/f/delivery"
    assert state.ahead == 1
    assert state.reason == ""


def test_readiness_survives_the_session_being_gone(repo: Path) -> None:
    """The property #217 wanted: nothing is asked of the child.

    No ``~/.claude/jobs`` entry exists for this tree in any of these tests —
    there is no session at all — and readiness still resolves. That is the
    whole difference from ``children[].kind == "pr"``, which needs the child
    to have volunteered something before the queue can say anything.
    """
    tree = _worktree(repo, "c-delivery", "feat/f/delivery")
    _commit(tree)

    assert delivery.ready(tree, repo_root=repo).ready


def test_uncommitted_work_is_refused_with_a_reason(repo: Path) -> None:
    tree = _worktree(repo, "c-delivery", "feat/f/delivery")
    _commit(tree)
    (tree / "work.py").write_text("x = 2\n", encoding="utf-8")  # modified, uncommitted

    state = delivery.ready(tree, repo_root=repo)

    assert not state.ready
    assert "uncommitted" in state.reason
    # The commit is still counted: the refusal is about the tree, and a
    # reason that said "nothing to deliver" would be wrong as well as unhelpful.
    assert state.ahead == 1


def test_an_untracked_file_makes_a_tree_dirty(repo: Path) -> None:
    """A child that wrote a module and never added it is not deliverable.

    Without ``--porcelain`` counting untracked files this reads as clean, and
    the push delivers a branch missing the file the piece was about.
    """
    tree = _worktree(repo, "c-delivery", "feat/f/delivery")
    _commit(tree)
    (tree / "forgotten.py").write_text("y = 2\n", encoding="utf-8")

    state = delivery.ready(tree, repo_root=repo)

    assert not state.ready
    assert "uncommitted" in state.reason


def test_a_branch_with_no_commits_is_refused(repo: Path) -> None:
    tree = _worktree(repo, "c-delivery", "feat/f/delivery")

    state = delivery.ready(tree, repo_root=repo)

    assert not state.ready
    assert state.ahead == 0
    assert "nothing to deliver" in state.reason


def test_a_tree_still_on_master_is_refused(repo: Path) -> None:
    """Pushing ``master`` from a delivery command is the accident to refuse."""
    # `git worktree add` refuses a branch already checked out elsewhere, and
    # master is checked out in `repo` itself. `--force` is what reproduces the
    # real shape: a tree sitting on master, which is what must be refused.
    tree = repo.parent / "mnemo-wt-c-onmaster"
    _run(["git", "worktree", "add", "--force", str(tree), "master"], cwd=repo)

    state = delivery.ready(tree, repo_root=repo)

    assert not state.ready
    assert "master" in state.reason


def test_a_detached_head_is_refused(repo: Path) -> None:
    tree = repo.parent / "mnemo-wt-c-detached"
    _run(["git", "worktree", "add", "--detach", str(tree)], cwd=repo)

    state = delivery.ready(tree, repo_root=repo)

    assert not state.ready
    assert state.branch is None
    assert "detached" in state.reason


def test_a_vanished_worktree_is_refused_not_crashed(repo: Path) -> None:
    state = delivery.ready(repo.parent / "mnemo-wt-c-gone", repo_root=repo)

    assert not state.ready
    assert "gone" in state.reason


def test_the_diffstat_describes_only_this_branchs_work(repo: Path) -> None:
    """``master...branch``, three dots. Two would count master's own commits.

    A long-running child branches, master moves on, and a two-dot diff reports
    everything that landed meanwhile as a deletion in the child's diff.
    """
    tree = _worktree(repo, "c-delivery", "feat/f/delivery")
    _commit(tree)
    # master moves on, after the child branched.
    (repo / "unrelated.py").write_text("z = 3\n", encoding="utf-8")
    _run(["git", "add", "unrelated.py"], cwd=repo)
    _run(["git", "commit", "-m", "unrelated"], cwd=repo)

    state = delivery.ready(tree, repo_root=repo)

    assert "1 file changed" in state.diffstat
    assert "unrelated" not in state.diffstat


# --- labelling -------------------------------------------------------------


def test_an_issue_child_is_labelled_with_its_number(repo: Path) -> None:
    tree = _worktree(repo, "211", "fix/issue-211")

    assert delivery.ready(tree, repo_root=repo).label == "#211"


def test_a_piece_child_is_labelled_bare(repo: Path) -> None:
    """``#c-delivery`` would read as an issue number that does not exist."""
    tree = _worktree(repo, "c-delivery", "feat/f/delivery")

    assert delivery.ready(tree, repo_root=repo).label == "c-delivery"


# --- which trees are ours --------------------------------------------------


def test_dispatch_worktrees_finds_every_child(repo: Path) -> None:
    _worktree(repo, "211", "fix/issue-211")
    _worktree(repo, "c-delivery", "feat/f/delivery")

    found = {p.name for p in delivery.dispatch_worktrees(repo_root=repo)}

    assert found == {"mnemo-wt-211", "mnemo-wt-c-delivery"}


def test_a_hand_made_worktree_is_not_offered_for_delivery(repo: Path) -> None:
    """``issue_for_cwd`` returns None for a path this dispatcher did not name.

    Offering it would label someone's own tree as a dispatch child and invite
    a push of work that was never dispatched.
    """
    _run(["git", "worktree", "add", "-b", "scratch",
          str(repo.parent / "mnemo-scratch")], cwd=repo)

    assert delivery.dispatch_worktrees(repo_root=repo) == []


def test_the_main_checkout_is_not_a_dispatch_worktree(repo: Path) -> None:
    assert delivery.dispatch_worktrees(repo_root=repo) == []


# --- naming a child --------------------------------------------------------


def test_a_piece_is_found_by_its_slug_with_or_without_the_prefix(repo: Path) -> None:
    tree = _worktree(repo, "c-delivery", "feat/f/delivery")

    assert delivery.find_worktree("c-delivery", repo_root=repo) == tree
    assert delivery.find_worktree("delivery", repo_root=repo) == tree


def test_an_issue_is_found_by_its_number_with_or_without_the_hash(repo: Path) -> None:
    tree = _worktree(repo, "211", "fix/issue-211")

    assert delivery.find_worktree("211", repo_root=repo) == tree
    assert delivery.find_worktree("#211", repo_root=repo) == tree


def test_an_unknown_name_resolves_to_nothing(repo: Path) -> None:
    assert delivery.find_worktree("nope", repo_root=repo) is None


def test_an_ambiguous_short_id_prefix_is_refused(repo: Path, monkeypatch) -> None:
    """Two sessions matching a prefix names neither.

    Picking one would push a branch the maintainer never looked at, which is
    the invariant the whole command is built around.
    """
    from mnemo.core.sessions.jobs import Session

    monkeypatch.setattr(
        "mnemo.core.sessions.jobs.read_sessions",
        lambda *a, **kw: [
            Session(short_id="ab11", cwd=str(repo.parent / "mnemo-wt-1")),
            Session(short_id="ab22", cwd=str(repo.parent / "mnemo-wt-2")),
        ],
    )

    assert delivery.find_worktree("ab", repo_root=repo) is None


def test_a_unique_short_id_prefix_resolves_to_its_cwd(repo: Path, monkeypatch) -> None:
    from mnemo.core.sessions.jobs import Session

    monkeypatch.setattr(
        "mnemo.core.sessions.jobs.read_sessions",
        lambda *a, **kw: [Session(short_id="ab11", cwd="/somewhere/mnemo-wt-211")],
    )

    assert delivery.find_worktree("ab1", repo_root=repo) == Path("/somewhere/mnemo-wt-211")


# --- the PR join: git, not session state -----------------------------------


def _gh(monkeypatch: pytest.MonkeyPatch, stdout: str, *, code: int = 0) -> list:
    """Capture the ``gh`` argv instead of running it. Returns the calls list."""
    calls: list[list[str]] = []

    def fake_run(args, **kw):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, code, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_pr_for_reads_the_url_out_of_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    _gh(monkeypatch, REAL_GH_PR)

    assert delivery.pr_for("fix/issue-214", repo_root="/repo") == (
        "https://github.com/xyrlan/mnemo/pull/214"
    )


def test_pr_for_asks_for_every_state_not_just_open(monkeypatch) -> None:
    """A merged branch's PR is closed, and must still be found.

    The caller uses this to decide whether delivering again would open a
    duplicate. "No open PR" is not "no PR", and the default is ``open``.
    """
    calls = _gh(monkeypatch, REAL_GH_PR)

    delivery.pr_for("fix/issue-214", repo_root="/repo")

    assert "--state" in calls[0] and "all" in calls[0]


def test_pr_for_is_none_when_no_pr_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    _gh(monkeypatch, REAL_GH_NONE)

    assert delivery.pr_for("feat/f/delivery", repo_root="/repo") is None


def test_pr_for_degrades_to_none_when_gh_fails(monkeypatch) -> None:
    """No gh, no network, not authenticated — all the same answer.

    This is a join that decorates a row, not a check anything depends on for
    safety. A missing answer must never fail the command that asked.
    """
    _gh(monkeypatch, "", code=1)

    assert delivery.pr_for("feat/f/delivery", repo_root="/repo") is None


def test_pr_for_survives_unparseable_gh_output(monkeypatch) -> None:
    _gh(monkeypatch, "not json at all")

    assert delivery.pr_for("feat/f/delivery", repo_root="/repo") is None


def test_pr_for_refuses_to_guess_from_an_empty_branch() -> None:
    assert delivery.pr_for("", repo_root="/repo") is None


def test_the_cache_asks_gh_once_per_branch(monkeypatch) -> None:
    """One ``gh`` call is ~400ms and ``--watch`` redraws every 2s."""
    calls = _gh(monkeypatch, REAL_GH_PR)
    cache = delivery.Cache()

    cache.pr_for("fix/issue-214", repo_root="/repo")
    cache.pr_for("fix/issue-214", repo_root="/repo")

    assert len(calls) == 1


def test_the_cache_remembers_an_absent_pr_too(monkeypatch) -> None:
    """A branch with no PR is the common case; re-asking is the common cost."""
    calls = _gh(monkeypatch, REAL_GH_NONE)
    cache = delivery.Cache()

    cache.pr_for("feat/f/delivery", repo_root="/repo")
    cache.pr_for("feat/f/delivery", repo_root="/repo")

    assert len(calls) == 1


def test_ready_reports_an_existing_pr_without_it_blocking_readiness(
    repo: Path, monkeypatch
) -> None:
    """A delivered branch is still clean and still ahead.

    What to do about the PR is the caller's decision, not readiness's.
    """
    monkeypatch.setattr(
        delivery, "pr_info",
        lambda b, **kw: delivery.PR(url="https://x/pull/1", state="OPEN"),
    )
    tree = _worktree(repo, "c-delivery", "feat/f/delivery")
    _commit(tree)

    state = delivery.ready(tree, repo_root=repo)

    assert state.ready
    assert state.pr == "https://x/pull/1"


# --- the queue's lookup ----------------------------------------------------


def test_pr_lookup_ignores_a_session_that_is_not_a_dispatch_child(repo) -> None:
    from mnemo.core.sessions.jobs import Session

    lookup = delivery.pr_lookup(repo_root=repo)

    assert lookup(Session(short_id="a", cwd="/Users/x/github/mnemo")) is None


def test_pr_lookup_derives_an_issue_branch_when_the_tree_is_gone(
    repo: Path, monkeypatch
) -> None:
    """A pruned worktree still has a derivable branch, for an issue child.

    ``branch_name(211)`` is ``fix/issue-211`` and needs nothing but the number.
    A piece slug needs its feature, which nothing at this point has — so that
    case returns None rather than guessing a branch that may not exist.
    """
    seen: list[str] = []
    monkeypatch.setattr(
        delivery, "pr_info",
        lambda branch, **kw: seen.append(branch)
        or delivery.PR(url="https://x/pull/9", state="OPEN"),
    )
    from mnemo.core.sessions.jobs import Session

    lookup = delivery.pr_lookup(repo_root=repo)
    found = lookup(Session(short_id="a", cwd="/gone/mnemo-wt-211"))

    assert found == "https://x/pull/9"
    assert seen == ["fix/issue-211"]


def test_pr_lookup_does_not_guess_a_branch_for_a_pruned_piece(repo) -> None:
    """``feat/<feature>/<slug>`` cannot be rebuilt from the slug alone."""
    from mnemo.core.sessions.jobs import Session

    lookup = delivery.pr_lookup(repo_root=repo)

    assert lookup(Session(short_id="a", cwd="/gone/mnemo-wt-c-delivery")) is None


def test_pr_lookup_reads_the_live_branch_off_the_worktree(
    repo: Path, monkeypatch
) -> None:
    """When the tree exists, its checked-out branch is the authority.

    Not a name derived from a convention: the tree knows what it is on, and a
    child that switched branches would otherwise be looked up under a branch
    it is no longer using.
    """
    tree = _worktree(repo, "c-delivery", "feat/dispatch-last-metre/delivery")
    seen: list[str] = []
    monkeypatch.setattr(
        delivery, "pr_info", lambda branch, **kw: seen.append(branch) or None,
    )
    from mnemo.core.sessions.jobs import Session

    delivery.pr_lookup(repo_root=repo)(Session(short_id="a", cwd=str(tree)))

    assert seen == ["feat/dispatch-last-metre/delivery"]


# --- pushing: never forced -------------------------------------------------


def test_push_sets_upstream_and_never_forces(monkeypatch) -> None:
    """A delivery that would overwrite the remote is a conflict to surface."""
    calls: list[list[str]] = []

    def fake_git(args, *, cwd):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(delivery, "_git", fake_git)

    delivery.push("feat/f/delivery", worktree="/tree")

    assert calls == [["push", "--set-upstream", "origin", "feat/f/delivery"]]
    assert not any("force" in part for part in calls[0])


def test_a_failed_push_raises_with_gits_own_message(monkeypatch) -> None:
    monkeypatch.setattr(
        delivery, "_git",
        lambda args, *, cwd: subprocess.CompletedProcess(
            args, 1, "", "! [rejected] feat/f/delivery -> feat/f/delivery"),
    )

    with pytest.raises(delivery.DeliveryError, match="rejected"):
        delivery.push("feat/f/delivery", worktree="/tree")


def test_open_pr_fills_the_body_from_the_commits(monkeypatch) -> None:
    """``--fill``: the commits were written by the child that did the work.

    A body generated here would summarise a diff nothing in this process read.
    """
    calls = _gh(monkeypatch, "https://github.com/xyrlan/mnemo/pull/220\n")

    url = delivery.open_pr("feat/f/delivery", worktree="/tree", title="c-delivery")

    assert url == "https://github.com/xyrlan/mnemo/pull/220"
    assert "--fill" in calls[0]
    assert "--title" in calls[0]


def test_open_pr_appends_a_closing_trailer_for_an_issue(monkeypatch) -> None:
    """#224: merging PR #223 did not close #222.

    ``--fill`` carried the child's commit message, which named the issue as
    ``fix(sessions): ... (#222)`` — a *reference*, not one of GitHub's closing
    keywords. The issue number is the one fact this process owns and the body
    does not carry, so it is appended after the body ``--fill`` wrote.
    """
    calls: list[list[str]] = []

    def fake_run(args, **kw):
        calls.append(list(args))
        argv = list(args)
        if argv[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(args, 0, REAL_FILLED_BODY, "")
        return subprocess.CompletedProcess(
            args, 0, "https://github.com/xyrlan/mnemo/pull/223\n", "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    url = delivery.open_pr(
        "fix/issue-222", worktree="/tree", title="#222: fix/issue-222", target=222,
    )

    assert url == "https://github.com/xyrlan/mnemo/pull/223"
    # create keeps --fill authoritative; the edit adds only the trailer.
    # `--body` alongside `--fill` would *overwrite* the filled body rather
    # than extend it, which is why this cannot be a single call.
    assert "--fill" in calls[0]
    edit = next(argv for argv in calls if argv[:3] == ["gh", "pr", "edit"])
    body = edit[edit.index("--body") + 1]
    assert body.endswith("Closes #222")
    # The child's prose is kept, not replaced.
    assert body.startswith(REAL_FILLED_BODY)


def test_open_pr_edits_the_pr_it_just_created_not_the_branchs(monkeypatch) -> None:
    """The edit is addressed by the URL ``create`` returned.

    ``gh pr edit`` with no argument resolves the PR from the *current branch*,
    which in a dispatch worktree is the right one only by coincidence — and
    silently the wrong one if the worktree is ever not on the branch pushed.
    """
    calls: list[list[str]] = []

    def fake_run(args, **kw):
        calls.append(list(args))
        argv = list(args)
        if argv[:3] == ["gh", "pr", "view"]:
            # Deliberately not the URL: a body echoing it would let an edit
            # addressed by branch pass this test on the `--body` value alone.
            return subprocess.CompletedProcess(args, 0, "did the work\n", "")
        return subprocess.CompletedProcess(
            args, 0, "https://github.com/xyrlan/mnemo/pull/223\n", "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    delivery.open_pr("fix/issue-222", worktree="/tree", title="t", target=222)

    url = "https://github.com/xyrlan/mnemo/pull/223"
    for verb in ("view", "edit"):
        argv = next(a for a in calls if a[:3] == ["gh", "pr", verb])
        # Positional, before any flag: `gh pr <verb> <url>`.
        assert argv[3] == url


def test_open_pr_adds_no_trailer_for_a_contract_piece(monkeypatch) -> None:
    """A ``c-<slug>`` piece has no issue to close.

    ``Closes #<n>`` is conditional on the target being an issue number, as
    #224 requires. A slug reaching the trailer would be a malformed reference
    or, worse, a number that closes an unrelated issue.
    """
    calls = _gh(monkeypatch, "https://github.com/xyrlan/mnemo/pull/220\n")

    delivery.open_pr(
        "feat/f/delivery", worktree="/tree", title="c-delivery", target="c-delivery",
    )

    assert len(calls) == 1
    assert "--fill" in calls[0]


def test_open_pr_without_a_target_stays_a_single_call(monkeypatch) -> None:
    """No target is the pre-#224 behaviour, unchanged."""
    calls = _gh(monkeypatch, "https://github.com/xyrlan/mnemo/pull/220\n")

    delivery.open_pr("feat/f/delivery", worktree="/tree", title="t")

    assert len(calls) == 1


def test_open_pr_keeps_the_pr_when_the_trailer_edit_fails(monkeypatch) -> None:
    """A failed edit must not read as a failed delivery.

    The PR exists and the branch is pushed. Raising here would send the
    maintainer to retry a ``deliver`` that would refuse as a duplicate, and
    the only thing actually missing is a line they can add in the UI.
    """
    calls: list[list[str]] = []

    def fake_run(args, **kw):
        calls.append(list(args))
        if list(args)[:3] == ["gh", "pr", "edit"]:
            return subprocess.CompletedProcess(args, 1, "", "could not update")
        return subprocess.CompletedProcess(
            args, 0, "https://github.com/xyrlan/mnemo/pull/223\n", "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    url = delivery.open_pr(
        "fix/issue-222", worktree="/tree", title="t", target=222,
    )

    assert url == "https://github.com/xyrlan/mnemo/pull/223"
    # The edit was attempted and refused, and the URL survived it.
    assert any(argv[:3] == ["gh", "pr", "edit"] for argv in calls)


@pytest.mark.parametrize(
    "body, closes",
    [
        # The two that define the bug. A conventional-commit subject naming
        # its issue is a *reference*; GitHub acts only on a keyword.
        ("fix(sessions): emit the derived booleans (#222)", False),
        ("Closes #222", True),
        ("closes #222", True),
        ("Fixes #222", True),
        ("Resolves: #222", True),
        ("Closed #222", True),
        # `#2220` shares a prefix with `#222` and must not read as it.
        ("Closes #2220", False),
        ("Closes #999", False),
        # `Discloses` ends in `closes` without being the keyword.
        ("Discloses #222", False),
    ],
)
def test_closes_already_reads_keywords_not_references(body, closes) -> None:
    """What separates a body that closes #222 from one that only names it."""
    assert delivery._closes_already(body, 222) is closes


def test_the_real_pr_223_body_carries_no_closing_keyword() -> None:
    """The regression, stated against the artifact that produced #224.

    If this ever reads True, the detection is over-matching and a real
    delivery would silently skip the trailer it exists to add.
    """
    assert delivery._closes_already(REAL_FILLED_BODY, 222) is False


def test_open_pr_keeps_the_pr_when_the_body_cannot_be_read(monkeypatch) -> None:
    """An unreadable body is not an excuse to edit blind, nor to raise.

    Appending to a body that could not be read would mean writing the trailer
    over whatever ``--fill`` wrote — destroying the child's prose to add one
    line. Doing nothing leaves the PR exactly as ``--fill`` made it.
    """
    calls: list[list[str]] = []

    def fake_run(args, **kw):
        calls.append(list(args))
        if list(args)[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(args, 1, "", "no such PR")
        return subprocess.CompletedProcess(
            args, 0, "https://github.com/xyrlan/mnemo/pull/223\n", "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    url = delivery.open_pr("fix/issue-222", worktree="/tree", title="t", target=222)

    assert url == "https://github.com/xyrlan/mnemo/pull/223"
    assert not any(argv[:3] == ["gh", "pr", "edit"] for argv in calls)


def test_open_pr_skips_the_trailer_when_the_body_already_closes_it(
    monkeypatch,
) -> None:
    """A child that wrote the trailer itself is not corrected into a double.

    ``build_prompt`` does not ask for it, but a child may write one anyway,
    and GitHub shows a repeated trailer verbatim.
    """
    calls: list[list[str]] = []

    def fake_run(args, **kw):
        calls.append(list(args))
        argv = list(args)
        if argv[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(
                args, 0, "did the work\n\nCloses #222\n", "",
            )
        return subprocess.CompletedProcess(
            args, 0, "https://github.com/xyrlan/mnemo/pull/223\n", "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    delivery.open_pr("fix/issue-222", worktree="/tree", title="t", target=222)

    assert not any(argv[:3] == ["gh", "pr", "edit"] for argv in calls)


def test_open_pr_raises_when_gh_refuses(monkeypatch) -> None:
    _gh(monkeypatch, "", code=1)

    with pytest.raises(delivery.DeliveryError):
        delivery.open_pr("feat/f/delivery", worktree="/tree", title="t")


def test_open_pr_returns_empty_rather_than_a_wrong_url(monkeypatch) -> None:
    """The PR was created either way; a blank reads as missing, a guess reads
    as actionable. Same trade as ``dispatch._short_id_from`` returning ''."""
    _gh(monkeypatch, "created\n")

    assert delivery.open_pr("b", worktree="/tree", title="t") == ""


# --- the PR join with its state, for landing (#236) ------------------------

# Verbatim `gh pr list --head feat/dispatch-last-metre/delivery --state all
# --json number,url,state --limit 1`, captured 2026-09-13: the PR of the first
# contract piece ever delivered, after it was merged.
REAL_GH_MERGED = (
    '[{"number":219,"state":"MERGED",'
    '"url":"https://github.com/xyrlan/mnemo/pull/219"}]\n'
)


def test_pr_info_reads_the_state_alongside_the_url(monkeypatch) -> None:
    """Landing needs to know a merged piece from an open one; ``pr_for`` does not."""
    calls = _gh(monkeypatch, REAL_GH_MERGED)

    info = delivery.pr_info("feat/dispatch-last-metre/delivery", repo_root="/repo")

    assert info is not None
    assert info.url == "https://github.com/xyrlan/mnemo/pull/219"
    assert info.state == "MERGED"
    assert "state" in calls[0][calls[0].index("--json") + 1]


def test_pr_info_is_none_when_no_pr_exists(monkeypatch) -> None:
    _gh(monkeypatch, REAL_GH_NONE)

    assert delivery.pr_info("feat/f/x", repo_root="/repo") is None


def test_pr_for_is_the_url_of_pr_info(monkeypatch) -> None:
    """One ``gh`` shape, two readers: ``pr_for`` must not drift from ``pr_info``."""
    _gh(monkeypatch, REAL_GH_MERGED)

    assert delivery.pr_for("feat/f/x", repo_root="/repo") == (
        delivery.pr_info("feat/f/x", repo_root="/repo").url
    )
