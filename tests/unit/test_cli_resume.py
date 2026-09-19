"""``mnemo resume`` — what it wakes, what it refuses, what it says (#393).

``subprocess`` is never reached: :func:`mnemo.core.sessions.wake.wake` is
replaced, so every assertion here is about the decision, not about the CLI.
The CLI half is measured in ``claude_cli``'s ``resume-under-own-id``.
"""
from __future__ import annotations

import time
from typing import Any

import pytest

from mnemo import cli
from mnemo.core.sessions import wake as wake_mod
from mnemo.core.sessions.jobs import Session
from mnemo.core.sessions.stalls import Kind, Stall

FREE_AT = int(time.time()) - 3600
LOCKED_UNTIL = int(time.time()) + 3600

FREE = Stall(kind=Kind.RATE_LIMIT, error="rate_limit", resets_at=FREE_AT,
             limit="five_hour")
NOT_YET = Stall(kind=Kind.RATE_LIMIT, error="rate_limit",
                resets_at=LOCKED_UNTIL, limit="five_hour")


def _child(short_id: str, issue: int, tree, needs: str) -> Session:
    return Session(short_id=short_id, state="blocked", tempo="blocked",
                   needs=needs, cwd=str(tree), live=False,
                   session_id=f"{short_id}-a282-4a1c-b04a-08441296047e",
                   updated_at="2026-09-19T03:51:57Z")


@pytest.fixture()
def bench(monkeypatch, tmp_path):
    """Two stalled children with real directories, and a recording wake."""
    trees = {}
    for issue in (380, 381):
        tree = tmp_path / f"mnemo-wt-{issue}"
        tree.mkdir()
        trees[issue] = tree

    state: dict[str, Any] = {
        "sessions": [
            _child("594436f2", 380, trees[380], "rate limited — wait and retry"),
            _child("aaaaaaaa", 381, trees[381], "rate limited — wait and retry"),
        ],
        "stalls": {"594436f2": FREE, "aaaaaaaa": FREE},
        "woken": [],
        "why": None,
    }

    from mnemo.cli.commands import resume as resume_mod
    from mnemo.core.sessions import jobs as jobs_mod
    from mnemo.core.sessions import stalls as stalls_mod

    monkeypatch.setattr(jobs_mod, "read_sessions",
                        lambda *a, **k: list(state["sessions"]))
    monkeypatch.setattr(stalls_mod, "stalls_for",
                        lambda sessions, **k: dict(state["stalls"]))

    def _wake(session_id, *, cwd, **kwargs):
        state["woken"].append((session_id, str(cwd)))
        return state["why"]

    monkeypatch.setattr(wake_mod, "wake", _wake)
    monkeypatch.chdir(tmp_path)
    state["trees"] = trees
    state["module"] = resume_mod
    return state


def test_one_command_wakes_every_child_the_reset_has_freed(bench, capsys) -> None:
    assert cli.main(["resume"]) == 0
    out = capsys.readouterr().out
    assert len(bench["woken"]) == 2
    assert "2 resumed" in out
    # The full session id, never the short one: the short id forks.
    assert all(len(sid) == 36 for sid, _ in bench["woken"])
    # In the child's own tree.
    assert bench["woken"][0][1] == str(bench["trees"][380])


def test_a_child_whose_window_has_not_reset_is_left_alone(bench, capsys) -> None:
    bench["stalls"]["aaaaaaaa"] = NOT_YET
    assert cli.main(["resume"]) == 0
    out = capsys.readouterr().out
    assert [sid for sid, _ in bench["woken"]] == ["594436f2-a282-4a1c-b04a-08441296047e"]
    assert "waiting:" in out and "aaaaaaaa" in out


def test_a_child_that_needs_an_answer_is_named_not_nudged(bench, capsys) -> None:
    """The issue's boundary: a question and a permission prompt are not this."""
    bench["sessions"][1] = _child("aaaaaaaa", 381, bench["trees"][381],
                                  "which of the two shapes did you mean?")
    bench["stalls"].pop("aaaaaaaa")
    assert cli.main(["resume"]) == 0
    out = capsys.readouterr().out
    assert [sid for sid, _ in bench["woken"]] == ["594436f2-a282-4a1c-b04a-08441296047e"]
    assert "needs you: aaaaaaaa" in out
    assert "claude attach aaaaaaaa" in out


def test_a_login_stall_is_never_woken(bench, capsys) -> None:
    bench["stalls"]["aaaaaaaa"] = Stall(kind=Kind.HUMAN,
                                        error="authentication_failed")
    assert cli.main(["resume"]) == 0
    assert len(bench["woken"]) == 1
    assert "needs you: aaaaaaaa" in capsys.readouterr().out


def test_naming_an_id_narrows_the_sweep(bench, capsys) -> None:
    assert cli.main(["resume", "594436f2"]) == 0
    assert [sid for sid, _ in bench["woken"]] == ["594436f2-a282-4a1c-b04a-08441296047e"]


def test_an_issue_number_names_the_child_too(bench, capsys) -> None:
    """What the maintainer actually remembers; the tree path already has it."""
    assert cli.main(["resume", "381"]) == 0
    assert [sid for sid, _ in bench["woken"]] == ["aaaaaaaa-a282-4a1c-b04a-08441296047e"]


def test_a_prefix_of_the_short_id_is_enough(bench) -> None:
    assert cli.main(["resume", "5944"]) == 0
    assert len(bench["woken"]) == 1


def test_dry_run_spends_nothing(bench, capsys) -> None:
    assert cli.main(["resume", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert bench["woken"] == []
    assert out.count("would resume:") == 2
    assert "nothing was spent" in out


def test_the_budget_is_stated_rather_than_implied(bench, capsys) -> None:
    """mnemo cannot read the remaining window; it must not pretend otherwise."""
    cli.main(["resume"])
    out = capsys.readouterr().out
    assert "one account window" in out
    assert "cannot see how much of it is left" in out


def test_a_gone_worktree_is_reported_not_woken_into(bench, capsys) -> None:
    import shutil

    shutil.rmtree(bench["trees"][381])
    assert cli.main(["resume"]) == 0
    out = capsys.readouterr().out
    assert len(bench["woken"]) == 1
    assert "worktree" in out and "is gone" in out
    assert "1 resumed, 1 failed" in out


def test_a_failed_wake_does_not_take_the_others_with_it(bench, capsys) -> None:
    bench["why"] = "no such session"
    assert cli.main(["resume"]) == 0
    out = capsys.readouterr().out
    assert len(bench["woken"]) == 2
    assert out.count("failed:") == 2
    assert "0 resumed, 2 failed" in out


def test_an_empty_queue_says_where_else_to_look(bench, capsys) -> None:
    bench["sessions"] = []
    assert cli.main(["resume"]) == 0
    out = capsys.readouterr().out
    assert "no stalled session in this repo" in out
    assert "mnemo resume --all" in out


def test_a_working_child_is_never_a_candidate(bench, capsys) -> None:
    """``--resume`` on a live session starts a copy in its worktree."""
    bench["sessions"] = [Session(short_id="594436f2", state="working",
                                 tempo="active", cwd=str(bench["trees"][380]),
                                 session_id="594436f2-a282-4a1c-b04a-08441296047e")]
    assert cli.main(["resume"]) == 0
    assert bench["woken"] == []


def test_resume_is_in_the_user_facing_help(capsys) -> None:
    assert cli.main(["help"]) == 0
    assert "resume" in capsys.readouterr().out
