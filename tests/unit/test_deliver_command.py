"""``mnemo deliver`` — the review, and the refusals that guard the push.

The invariant these assert is the reason the command exists in this shape: a
child must never acquire permission it was denied, and the maintainer must see
the diff before anything is pushed (#215). So the tests that matter most are
the ones proving that **nothing is pushed** — by an unnamed id, by a flag, by
a tree that is not ready.

``--review`` is exercised against real git worktrees for the same reason
:mod:`tests.unit.test_delivery` is: git is the authority here, and a stubbed
git tests the stub. The push path is stubbed, because a unit test that pushes
is not a unit test.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pytest

from mnemo.cli.commands import deliver
from mnemo.core.sessions import delivery


def _run(args, *, cwd) -> None:
    subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "mnemo"
    root.mkdir()
    _run(["git", "init", "-b", "master"], cwd=root)
    _run(["git", "config", "user.email", "t@example.com"], cwd=root)
    _run(["git", "config", "user.name", "t"], cwd=root)
    (root / "README.md").write_text("base\n")
    _run(["git", "add", "README.md"], cwd=root)
    _run(["git", "commit", "-m", "base"], cwd=root)
    return root


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch):
    """Never reach GitHub, and never push. Each test opts in to what it needs."""
    monkeypatch.setattr(delivery, "pr_for", lambda branch, **kw: None)


@pytest.fixture
def in_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(deliver, "_repo_root", lambda: repo)
    return repo


def _ready_tree(repo: Path, target: str, branch: str) -> Path:
    tree = repo.parent / f"mnemo-wt-{target}"
    _run(["git", "worktree", "add", "-b", branch, str(tree)], cwd=repo)
    (tree / "work.py").write_text("x = 1\n")
    _run(["git", "add", "work.py"], cwd=tree)
    _run(["git", "commit", "-m", "work"], cwd=tree)
    return tree


def _args(**kw) -> argparse.Namespace:
    return argparse.Namespace(ids=kw.pop("ids", []), review=kw.pop("review", False), **kw)


@pytest.fixture
def pushed(monkeypatch: pytest.MonkeyPatch) -> list:
    """Record what would have been pushed, and push nothing."""
    calls: list[tuple] = []
    monkeypatch.setattr(
        delivery, "push",
        lambda branch, *, worktree: calls.append(("push", branch)),
    )
    def _open_pr(branch, *, worktree, title, target=None):
        # `target` is recorded, not ignored: #224 is a bug about the one fact
        # this command owns never reaching the PR, so the wiring is the thing
        # worth asserting on.
        calls.append(("pr", branch, target))
        return "https://x/pull/1"

    monkeypatch.setattr(delivery, "open_pr", _open_pr)
    return calls


# --- the invariant: nothing is pushed that was not named -------------------


def test_review_pushes_nothing(in_repo: Path, pushed: list, capsys) -> None:
    """``--review`` is read-only, and that is the whole of its contract."""
    _ready_tree(in_repo, "c-delivery", "feat/f/delivery")

    assert deliver.cmd_deliver(_args(review=True)) == 0
    assert pushed == []


def test_there_is_no_flag_that_delivers_everything() -> None:
    """One flag approving N children is the failure mode this prevents.

    Asserted against the parser, because a future ``--all`` would be added
    there and would look locally reasonable. Naming an id is the approval.
    """
    from mnemo.cli.parser import _build_parser

    parser = _build_parser()
    action = next(
        a for a in parser._subparsers._group_actions[0].choices["deliver"]._actions
        if "--review" in a.option_strings
    )

    flags = {
        flag
        for a in parser._subparsers._group_actions[0].choices["deliver"]._actions
        for flag in a.option_strings
    }
    assert "--all" not in flags
    assert action.option_strings == ["--review"]


def test_delivering_nothing_named_is_refused(in_repo: Path, pushed: list, capsys) -> None:
    assert deliver.cmd_deliver(_args()) == 1
    assert pushed == []
    assert "--review" in capsys.readouterr().out


def test_review_and_ids_together_are_refused(in_repo: Path, pushed: list) -> None:
    """A read-only report must not become a preamble to a push."""
    assert deliver.cmd_deliver(_args(ids=["c-delivery"], review=True)) == 1
    assert pushed == []


def test_a_tree_that_is_not_ready_is_refused_never_pushed(
    in_repo: Path, pushed: list, capsys
) -> None:
    tree = _ready_tree(in_repo, "c-delivery", "feat/f/delivery")
    (tree / "dirty.py").write_text("x = 2\n")  # uncommitted

    assert deliver.cmd_deliver(_args(ids=["c-delivery"])) == 1
    assert pushed == []
    assert "uncommitted" in capsys.readouterr().out


def test_an_unknown_id_is_refused_and_pushes_nothing(
    in_repo: Path, pushed: list, capsys
) -> None:
    assert deliver.cmd_deliver(_args(ids=["c-nope"])) == 1
    assert pushed == []
    assert "no dispatch worktree" in capsys.readouterr().out


def test_only_the_named_child_is_delivered(in_repo: Path, pushed: list) -> None:
    """The other tree is equally ready and must be left alone."""
    _ready_tree(in_repo, "c-delivery", "feat/f/delivery")
    _ready_tree(in_repo, "c-other", "feat/f/other")

    assert deliver.cmd_deliver(_args(ids=["c-delivery"])) == 0

    assert pushed == [
        ("push", "feat/f/delivery"), ("pr", "feat/f/delivery", "c-delivery"),
    ]


def test_the_issue_number_reaches_open_pr(in_repo: Path, pushed: list) -> None:
    """#224: the trailer can only be written if the target is threaded through.

    `open_pr` decides whether to append `Closes #<n>` from this argument. The
    command recovers it from the worktree path and is the only thing that
    knows it, so a `None` here is the whole bug with the fix still in place.
    """
    _ready_tree(in_repo, "222", "fix/issue-222")

    assert deliver.cmd_deliver(_args(ids=["222"])) == 0

    assert pushed == [("push", "fix/issue-222"), ("pr", "fix/issue-222", 222)]


def test_each_named_child_is_delivered(in_repo: Path, pushed: list) -> None:
    _ready_tree(in_repo, "c-delivery", "feat/f/delivery")
    _ready_tree(in_repo, "c-other", "feat/f/other")

    assert deliver.cmd_deliver(_args(ids=["c-delivery", "c-other"])) == 0

    assert [c[1] for c in pushed if c[0] == "push"] == [
        "feat/f/delivery", "feat/f/other",
    ]


def test_one_refusal_does_not_strand_the_others(
    in_repo: Path, pushed: list, capsys
) -> None:
    """Children are independent; a bad name in the middle is not an abort."""
    _ready_tree(in_repo, "c-delivery", "feat/f/delivery")

    code = deliver.cmd_deliver(_args(ids=["c-nope", "c-delivery"]))

    assert code == 1  # something was refused
    assert ("push", "feat/f/delivery") in pushed  # and the good one still went


def test_an_existing_pr_stops_a_second_delivery(
    in_repo: Path, pushed: list, monkeypatch, capsys
) -> None:
    """Delivering twice would open a duplicate PR for the same branch."""
    _ready_tree(in_repo, "c-delivery", "feat/f/delivery")
    monkeypatch.setattr(delivery, "pr_for", lambda b, **kw: "https://x/pull/7")

    assert deliver.cmd_deliver(_args(ids=["c-delivery"])) == 1
    assert pushed == []
    assert "https://x/pull/7" in capsys.readouterr().out


def test_a_failed_push_does_not_open_a_pr(in_repo: Path, monkeypatch, capsys) -> None:
    _ready_tree(in_repo, "c-delivery", "feat/f/delivery")
    opened: list = []

    def boom(branch, *, worktree):
        raise delivery.DeliveryError("! [rejected]")

    monkeypatch.setattr(delivery, "push", boom)
    monkeypatch.setattr(
        delivery, "open_pr",
        lambda *a, **kw: opened.append(1) or "",
    )

    assert deliver.cmd_deliver(_args(ids=["c-delivery"])) == 1
    assert opened == []
    assert "rejected" in capsys.readouterr().out


def test_a_push_that_landed_is_reported_even_when_the_pr_fails(
    in_repo: Path, monkeypatch, capsys
) -> None:
    """The branch is on the remote; a retry must not read as a fresh start."""
    _ready_tree(in_repo, "c-delivery", "feat/f/delivery")
    monkeypatch.setattr(delivery, "push", lambda branch, *, worktree: None)

    def boom(branch, *, worktree, title, target=None):
        raise delivery.DeliveryError("no default branch")

    monkeypatch.setattr(delivery, "open_pr", boom)

    assert deliver.cmd_deliver(_args(ids=["c-delivery"])) == 1
    out = capsys.readouterr().out
    assert "pushed" in out and "gh pr create failed" in out


# --- the review ------------------------------------------------------------


def test_review_buckets_ready_from_not_ready(in_repo: Path, capsys) -> None:
    _ready_tree(in_repo, "c-delivery", "feat/f/delivery")
    tree = in_repo.parent / "mnemo-wt-c-empty"
    _run(["git", "worktree", "add", "-b", "feat/f/empty", str(tree)], cwd=in_repo)

    deliver.cmd_deliver(_args(review=True))
    out = capsys.readouterr().out

    assert out.index("PRONTAS") < out.index("NÃO PRONTAS")
    assert "c-delivery" in out and "c-empty" in out
    assert "nothing to deliver" in out


def test_review_shows_the_diffstat_of_what_would_be_delivered(
    in_repo: Path, capsys
) -> None:
    _ready_tree(in_repo, "c-delivery", "feat/f/delivery")

    deliver.cmd_deliver(_args(review=True))

    assert "1 file changed" in capsys.readouterr().out


def test_review_hints_the_exact_command_with_every_id_spelled_out(
    in_repo: Path, capsys
) -> None:
    """The hint saves typing, not the decision: the ids stay visible and
    editable rather than collapsing into a flag."""
    _ready_tree(in_repo, "c-delivery", "feat/f/delivery")
    _ready_tree(in_repo, "c-other", "feat/f/other")

    deliver.cmd_deliver(_args(review=True))

    assert "mnemo deliver c-delivery c-other" in capsys.readouterr().out


def test_review_names_an_existing_pr_rather_than_hiding_the_row(
    in_repo: Path, monkeypatch, capsys
) -> None:
    """A branch pushed again after review comments is the same row shape."""
    _ready_tree(in_repo, "c-delivery", "feat/f/delivery")
    monkeypatch.setattr(delivery, "pr_for", lambda b, **kw: "https://x/pull/7")

    deliver.cmd_deliver(_args(review=True))

    assert "https://x/pull/7" in capsys.readouterr().out


def test_review_of_an_empty_repo_says_so_and_succeeds(in_repo: Path, capsys) -> None:
    """"Nothing to deliver" is a successful report: this is the command a
    maintainer runs to find out."""
    assert deliver.cmd_deliver(_args(review=True)) == 0
    assert "no dispatch worktrees" in capsys.readouterr().out


def test_outside_a_git_repo_the_command_refuses(monkeypatch, capsys) -> None:
    monkeypatch.setattr(deliver, "_repo_root", lambda: None)

    assert deliver.cmd_deliver(_args(review=True)) == 1
    assert "not inside a git repository" in capsys.readouterr().out


# --- wiring ----------------------------------------------------------------


def test_deliver_is_registered_as_a_command() -> None:
    from mnemo.cli.parser import COMMANDS

    assert "deliver" in COMMANDS


def test_the_parser_accepts_the_two_shapes() -> None:
    from mnemo.cli.parser import _build_parser

    parser = _build_parser()

    assert parser.parse_args(["deliver", "--review"]).review is True
    assert parser.parse_args(["deliver", "c-delivery", "211"]).ids == [
        "c-delivery", "211",
    ]


def test_ids_are_strings_not_ints() -> None:
    """A piece slug is not a number, and ``#211`` is not either.

    ``dispatch`` takes ``type=int`` because an issue is all it can dispatch;
    this takes three spellings, and typing them as int would refuse two.
    """
    from mnemo.cli.parser import _build_parser

    assert _build_parser().parse_args(["deliver", "211"]).ids == ["211"]
