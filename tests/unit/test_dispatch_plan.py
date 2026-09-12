"""``mnemo dispatch`` — the pure half: naming, labelling and the child prompt.

Every constraint asserted here cost a failed attempt during the real
three-issue dispatch on 2026-09-12 (#197). They are encoded, not rediscovered.

The spawn/worktree half lives in :mod:`tests.unit.test_dispatch_worktrees`.
"""
from __future__ import annotations

from mnemo.core import dispatch


# --- the mapping: a naming convention, not a new state file ----------------
#
# The issue proposed recording issue -> short_id -> worktree somewhere. It is
# already recoverable: the dispatcher chooses the worktree path, so the path
# *is* the record. Nothing to write, nothing to reconcile, nothing to rot.


def test_worktree_path_encodes_the_issue_number() -> None:
    path = dispatch.worktree_path(197, repo_root="/Users/x/github/mnemo")

    assert str(path) == "/Users/x/github/mnemo-wt-197"


def test_worktree_is_a_sibling_never_inside_the_repo() -> None:
    """Inside the repo it would be swept by `add -A` and indexed by mnemo."""
    path = dispatch.worktree_path(197, repo_root="/Users/x/github/mnemo")

    assert "/mnemo/" not in str(path)


def test_worktree_of_a_worktree_anchors_on_the_main_repo() -> None:
    """Dispatching from inside a child's tree must not stack suffixes.

    Measured: run from ``mnemo-wt-197`` the naive form yields
    ``mnemo-wt-197-wt-198``, and the next generation nests again. Every tree
    is a sibling of the *repo*, whichever tree the dispatcher is standing in.
    """
    path = dispatch.worktree_path(198, repo_root="/Users/x/github/mnemo-wt-197")

    assert str(path) == "/Users/x/github/mnemo-wt-198"


def test_issue_for_cwd_round_trips_through_worktree_path() -> None:
    nested = dispatch.worktree_path(198, repo_root="/Users/x/github/mnemo-wt-197")

    assert dispatch.issue_for_cwd(nested) == 198


def test_branch_name_is_the_issue_branch() -> None:
    assert dispatch.branch_name(197) == "fix/issue-197"


def test_issue_for_cwd_recovers_the_number_the_dispatcher_chose() -> None:
    assert dispatch.issue_for_cwd("/Users/x/github/mnemo-wt-197") == 197


def test_issue_for_cwd_ignores_an_undispatched_checkout() -> None:
    assert dispatch.issue_for_cwd("/Users/x/github/mnemo") is None
    assert dispatch.issue_for_cwd(None) is None


def test_issue_for_cwd_ignores_a_non_numeric_suffix() -> None:
    assert dispatch.issue_for_cwd("/Users/x/github/mnemo-wt-feature") is None


def test_issue_for_cwd_survives_a_trailing_slash() -> None:
    assert dispatch.issue_for_cwd("/Users/x/github/mnemo-wt-197/") == 197


# --- the prompt: the issue, not a solution ---------------------------------
#
# The #187 child was handed a prescribed approach ("implement slug-keyed
# dedupe"), refused it, and was right: the instructed change would have
# reopened #177 at vault scale. A prompt that prescribes can override a
# correct refusal, so the dispatcher hands over context and scope only.


ISSUE_BODY = "The gate demotes via replace(page, type='reference'), keeping the slug."


def test_prompt_carries_the_issue_body_verbatim() -> None:
    prompt = dispatch.build_prompt(187, title="cross-type dupes", body=ISSUE_BODY)

    assert ISSUE_BODY in prompt


def test_prompt_names_the_issue_so_the_child_can_read_it_itself() -> None:
    prompt = dispatch.build_prompt(187, title="cross-type dupes", body=ISSUE_BODY)

    assert "gh issue view 187" in prompt


def test_prompt_carries_the_scope_guard() -> None:
    prompt = dispatch.build_prompt(187, title="cross-type dupes", body=ISSUE_BODY)

    assert "do not merge or push without asking" in prompt.lower()


def test_prompt_invites_refusal_rather_than_prescribing() -> None:
    """The finding of #197: the issue outperformed the dispatching prompt."""
    prompt = dispatch.build_prompt(187, title="cross-type dupes", body=ISSUE_BODY)

    assert "refus" in prompt.lower()


def test_prompt_never_prescribes_an_approach() -> None:
    """No caller can smuggle a preferred solution in; there is no parameter."""
    import inspect

    params = set(inspect.signature(dispatch.build_prompt).parameters)

    assert params == {"issue", "title", "body"}


def test_prompt_tolerates_an_empty_body() -> None:
    """A bodyless issue still dispatches; the child reads it with gh."""
    prompt = dispatch.build_prompt(187, title="cross-type dupes", body="")

    assert "gh issue view 187" in prompt
