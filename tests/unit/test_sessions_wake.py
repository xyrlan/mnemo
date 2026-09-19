"""Waking a stalled child, and the two things that must not happen (#393).

A copy running in a child's worktree, and a second message the child reads as
its maintainer's.
"""
from __future__ import annotations

import subprocess
from typing import Any

import pytest

from mnemo.core.sessions import detector, wake

FULL = "594436f2-a282-4a1c-b04a-08441296047e"
SHORT = "594436f2"


class _Run:
    """Records the argv it was handed and replays a canned result."""

    def __init__(self, *, stdout: str = "", stderr: str = "", code: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self._result = subprocess.CompletedProcess([], code, stdout, stderr)

    def __call__(self, args: Any, **kwargs: Any):
        self.calls.append(list(args))
        self.kwargs.append(kwargs)
        return self._result


WOKE = ("note: woke session 594436f2 with its saved options (--model, "
        "--permission-mode).\nbackgrounded · 594436f2\n")
COPIED = ("note: started a copy of that conversation as c3ff22ea. To continue "
          "a session under its own id, pass its full session id (lowercase, as "
          "`claude agents --json` prints it) to --resume.\nbackgrounded · c3ff22ea\n")


def test_the_short_id_is_refused_before_anything_runs(monkeypatch) -> None:
    """The measured trap: the short id does not fail, it forks."""
    run = _Run()
    monkeypatch.setattr(subprocess, "run", run)
    why = wake.wake(SHORT, cwd="/tmp")
    assert why and "copy" in why
    assert run.calls == [], "a short id must never reach the CLI"


@pytest.mark.parametrize("bad", ["", None, "594436f2-a282-4a1c-b04a", FULL.upper(),
                                 FULL.replace("-", ""), FULL + "0"])
def test_only_a_full_lowercase_id_passes_the_shape_check(bad) -> None:
    assert wake.is_full_session_id(bad) is False


def test_a_full_id_is_woken_in_the_child_s_own_tree(monkeypatch) -> None:
    run = _Run(stdout=WOKE)
    monkeypatch.setattr(subprocess, "run", run)
    assert wake.wake(FULL, cwd="/tmp/tree") is None
    assert run.calls == [["claude", "--bg", "--resume", FULL, wake.NUDGE]]
    assert run.kwargs[0]["cwd"] == "/tmp/tree"


def test_a_copy_the_cli_made_anyway_is_reported_not_swallowed(monkeypatch) -> None:
    """If the assumption moves, a second session is running in the tree now."""
    monkeypatch.setattr(subprocess, "run", _Run(stdout=COPIED))
    why = wake.wake(FULL, cwd="/tmp/tree")
    assert why and "resume-under-own-id" in why and "c3ff22ea" in why


def test_a_failed_wake_is_a_message_not_an_exception(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", _Run(code=1, stderr="no such session"))
    assert wake.wake(FULL, cwd="/tmp") == "no such session"


def test_a_missing_cli_is_a_message_too(monkeypatch) -> None:
    def _boom(*a, **k):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert "claude" in (wake.wake(FULL, cwd="/tmp") or "")


# --- what the woken child is told ------------------------------------------


def test_wake_takes_no_message(monkeypatch) -> None:
    """Structurally, not carefully: a `--resume` turn lands as the maintainer's.

    Measured on 2.1.278 — ``origin.kind: "human"``, ``promptSource:
    "typed"``, the opening prompt's own shape. A ``text=`` parameter here
    would be a way to put words in the maintainer's mouth, so there is none.
    """
    import inspect

    params = set(inspect.signature(wake.wake).parameters)
    assert params == {"session_id", "cwd", "timeout"}


def test_the_nudge_grants_nothing_and_points_back_at_the_prompt() -> None:
    assert wake.NUDGE.startswith(wake.NUDGE_PREFIX)
    assert "grants you nothing" in wake.NUDGE
    assert "opening prompt" in wake.NUDGE
    assert "git status" in wake.NUDGE
    # None of the words a grant is made of.
    lowered = wake.NUDGE.lower()
    for word in ("you may push", "you may merge", "approved", "permission to"):
        assert word not in lowered


def test_a_wake_is_not_counted_as_the_maintainer_answering() -> None:
    """Pinned to the detector the way ``inbox.NOTICE_PREFIX`` is (#357)."""
    assert wake.NUDGE_PREFIX in detector.SYNTHETIC_PREFIXES
    record = {"type": "user", "message": {"role": "user", "content": wake.NUDGE}}
    assert detector.is_human_turn(record) is False
