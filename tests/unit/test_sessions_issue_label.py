"""Labelling a dispatched child by its issue (#197).

The queue's label was guesswork: Claude Code sets ``nameSource="auto"`` and
infers a title from the transcript. Measured on the four real children of the
2026-09-12 dispatch, #193 came back as "recall harness hit_slugs migration" —
readable, but the issue number is gone, and the issue number is the one thing
the maintainer is tracking.

The fix reuses ``cwd``, which the dispatcher itself chose, rather than adding
a state file mnemo would have to write, key by ``short_id`` and reconcile
against jobs that get pruned. See :mod:`mnemo.core.dispatch`.
"""
from __future__ import annotations

from mnemo.core.sessions.jobs import Session


def test_label_leads_with_the_issue_for_a_dispatched_child() -> None:
    s = Session(
        short_id="61a33c5f",
        name="recall harness hit_slugs migration",
        cwd="/Users/x/github/mnemo-wt-193",
    )

    assert s.label.startswith("#193")
    assert "recall harness" in s.label


def test_label_is_unchanged_outside_a_dispatched_worktree() -> None:
    s = Session(short_id="a1b2", name="auth refactor", cwd="/Users/x/github/mnemo")

    assert s.label == "auth refactor"


def test_label_falls_back_to_the_issue_alone_when_nothing_is_named() -> None:
    s = Session(short_id="a1b2", cwd="/Users/x/github/mnemo-wt-197")

    assert s.label == "#197"


def test_label_still_prefers_intent_over_short_id() -> None:
    s = Session(short_id="a1b2", intent="Work on issue #193 in this repo")

    assert s.label == "Work on issue #193 in this repo"


def test_label_stays_within_the_render_column() -> None:
    """render_queue pads the label to 22 columns; a longer one skews the row."""
    s = Session(
        short_id="a1b2",
        name="an extremely long inferred session title that runs on and on",
        cwd="/Users/x/github/mnemo-wt-197",
    )

    assert len(s.label) <= 40
    assert s.label.startswith("#197")
