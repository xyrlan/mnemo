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


# --- saved options the CLI no longer honours (#822) -------------------------

OLD_LEAN_BYPASS = ["--permission-mode", "bypassPermissions", "--setting-sources",
                   "project,local", "--strict-mcp-config", "--mcp-config", "m.json",
                   "--settings", "s.json", "--model", "opus"]


@pytest.mark.parametrize("flags, expected", [
    (OLD_LEAN_BYPASS, True),
    (["--setting-sources", "project", "--permission-mode", "bypassPermissions"], True),
    (["--permission-mode", "bypassPermissions", "--setting-sources", "user,project,local",
      "--strict-mcp-config"], False),
    (["--permission-mode", "bypassPermissions", "--setting-sources", "project, user"], False),
    (["--permission-mode", "bypassPermissions", "--model", "opus"], False),
    (["--setting-sources", "project,local", "--strict-mcp-config"], False),
    (["--permission-mode", "auto", "--setting-sources", "project,local"], False),
    (["--setting-sources", "project,local", "--permission-mode"], False),
    ([], False),
], ids=["pre-822-lean-bypass", "order", "822-lean-bypass", "spaced", "full-bypass",
        "lean-auto", "lean-explicit-auto", "dangling-flag", "nothing"])
def test_which_saved_options_wake_into_default(flags, expected) -> None:
    assert wake.wakes_into_default(flags) is expected


def test_a_pre_822_lean_bypass_child_is_not_woken(monkeypatch) -> None:
    """Measured on 2.1.321: woken, it runs ``default`` and stops at its first
    prompt; flags passed with the wake start a copy. So nothing is run."""
    run = _Run(stdout=WOKE)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(wake, "saved_flags", lambda session_id: OLD_LEAN_BYPASS)

    why = wake.wake(FULL, cwd="/tmp/tree")

    assert why and SHORT in why and "bg-bypass-needs-user-settings" in why
    assert "default" in why and "dispatch" in why
    assert run.calls == []


def test_a_child_dispatched_since_822_is_woken_as_before(monkeypatch) -> None:
    run = _Run(stdout=WOKE)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(wake, "saved_flags", lambda session_id: [
        "--permission-mode", "bypassPermissions", "--setting-sources", "user,project,local"])

    assert wake.wake(FULL, cwd="/tmp/tree") is None
    assert run.calls == [["claude", "--bg", "--resume", FULL, wake.NUDGE]]


def test_saved_flags_come_from_the_first_account_that_has_the_session(monkeypatch, tmp_path) -> None:
    from mnemo.core.sessions import accounts

    missing = tmp_path / "a" / "state.json"
    broken = tmp_path / "b" / "state.json"
    broken.parent.mkdir()
    broken.write_text("{not json", encoding="utf-8")
    good = tmp_path / "c" / "state.json"
    good.parent.mkdir()
    good.write_text('{"respawnFlags": ["--model", "haiku"]}', encoding="utf-8")
    asked = []

    def paths(short_id):
        asked.append(short_id)
        return [missing, broken, good]

    monkeypatch.setattr(accounts, "state_paths", paths)
    assert wake.saved_flags(FULL) == ["--model", "haiku"]
    assert asked == [SHORT]

    monkeypatch.setattr(accounts, "state_paths", lambda short_id: [missing, broken])
    assert wake.saved_flags(FULL) == []


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
