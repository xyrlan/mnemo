"""The read-only posture: a child that investigates and cannot edit files."""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from mnemo.cli.commands import dispatch as cli_dispatch
from mnemo.core import dispatch


class _Spawn:
    """Records the argv instead of running it."""

    def __init__(self, stdout: str = "abc12345\n") -> None:
        self.args: list[str] = []
        self.stdout = stdout

    def __call__(self, args, **kwargs):
        self.args = list(args)
        return type("R", (), {"returncode": 0, "stdout": self.stdout, "stderr": ""})()


# Every test below that calls `spawn_child` directly carries
# `@pytest.mark.real_spawn`. `tests/conftest.py`'s autouse
# `_no_real_detached_jobs` otherwise replaces `spawn_child` itself with a no-op,
# so the test would measure the stub rather than the argv — and the
# byte-identical test would pass for a hollow reason, comparing two empty lists
# down the same no-op path. Nine test modules already carry it for this reason.
READ_ONLY_TOOLS = ("Edit", "Write", "NotebookEdit")


@pytest.mark.real_spawn
def test_read_only_child_is_spawned_with_the_file_tools_closed(monkeypatch, tmp_path):
    spawn = _Spawn()
    monkeypatch.setattr(dispatch.subprocess, "run", spawn)
    monkeypatch.setattr(dispatch.claude_cli, "require_short_id", lambda out: "abc12345")

    dispatch.spawn_child("do the thing", cwd=tmp_path, read_only=True)

    assert "--disallowedTools" in spawn.args
    at = spawn.args.index("--disallowedTools")
    assert spawn.args[at + 1:at + 1 + len(READ_ONLY_TOOLS)] == list(READ_ONLY_TOOLS)


@pytest.mark.real_spawn
@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"lean": False},
        {"lean": False, "model": "haiku"},
        {"model": "haiku", "effort": "high"},
    ],
    ids=["defaults", "full-profile", "full-profile+model", "model+effort"],
)
def test_the_prompt_is_never_eaten_by_the_variadic_tool_list(monkeypatch, tmp_path, kwargs):
    """`--disallowedTools` consumes tokens until a flag, so the prompt must be
    fenced off explicitly. Relying on a later flag to stop it holds only while
    one happens to be present: `--full-profile` with no model puts the prompt
    straight after the list, where the CLI reads it as a tool name and the run
    dies with "Input must be provided either through stdin or as a prompt
    argument" (measured 2026-09-17).
    """
    spawn = _Spawn()
    monkeypatch.setattr(dispatch.subprocess, "run", spawn)
    monkeypatch.setattr(dispatch.claude_cli, "require_short_id", lambda out: "abc12345")

    dispatch.spawn_child("THE PROMPT", cwd=tmp_path, read_only=True, **kwargs)

    assert spawn.args[-1] == "THE PROMPT"
    at = spawn.args.index("--disallowedTools")
    after = spawn.args[at + 1 + len(READ_ONLY_TOOLS)]
    assert after == "--", f"the tool list must be closed explicitly, got {after!r}"


@pytest.mark.real_spawn
def test_a_normal_child_argv_is_byte_identical(monkeypatch, tmp_path):
    """A dispatch that is not read-only runs the command it ran before."""
    plain, ro = _Spawn(), _Spawn()
    monkeypatch.setattr(dispatch.claude_cli, "require_short_id", lambda out: "abc12345")

    monkeypatch.setattr(dispatch.subprocess, "run", plain)
    dispatch.spawn_child("do the thing", cwd=tmp_path)

    monkeypatch.setattr(dispatch.subprocess, "run", ro)
    dispatch.spawn_child("do the thing", cwd=tmp_path, read_only=False)

    assert plain.args == ro.args
    assert "--disallowedTools" not in plain.args


def test_the_analysis_prompt_does_not_ask_for_an_implementation():
    prompt = dispatch.build_prompt(
        361, title="dedupe suppresses the strongest matches",
        body="Measured: 30.7% of silences.", read_only=True,
    )

    assert "Run the full test suite before claiming the work is done." not in prompt
    assert "changelog.d" not in prompt
    assert "investigate" in prompt.lower()


def test_the_analysis_prompt_keeps_the_refusal_licence():
    """The passage that licenses a measured refusal reads correctly for an
    investigator, and is the reason the outcome was reachable at all.
    """
    prompt = dispatch.build_prompt(361, title="t", body="b", read_only=True)

    assert "No approach is prescribed" in prompt


def test_the_analysis_prompt_names_the_issue_and_the_worktree():
    prompt = dispatch.build_prompt(361, title="the title", body="the body", read_only=True)

    assert "#361" in prompt
    assert "the title" in prompt
    assert "the body" in prompt
    assert dispatch.branch_name(361) in prompt


def test_the_implement_prompt_is_unchanged():
    """Byte-identical for every existing caller."""
    before = dispatch.build_prompt(361, title="t", body="b")
    after = dispatch.build_prompt(361, title="t", body="b", read_only=False)

    assert before == after
    assert "Run the full test suite before claiming the work is done." in before


def test_the_read_only_closing_asks_for_a_comment_not_a_push():
    prompt = dispatch.build_prompt(361, title="t", body="b", read_only=True)

    assert "gh issue comment 361" in prompt
    assert "commit ahead of the base" not in prompt
    assert "mnemo deliver" not in prompt


def test_the_read_only_closing_keeps_the_report_and_the_stop():
    """Neither depends on the posture, and the briefing is the artefact the
    implement-path already calls the most valuable in the system.

    Asserted on the unwrapped source rather than the rendered prompt: `_wrap`
    breaks step 1 between "session's" and "briefing", so any substring long
    enough to be meaningful spans a newline and an indent.
    """
    prompt = dispatch.build_prompt(361, title="t", body="b", read_only=True)

    assert dispatch._READ_ONLY_CLOSING_STEPS[0] == dispatch._CLOSING_STEPS[0]
    assert "This is the only copy" in prompt
    assert "claude stop" in prompt


def test_the_implement_closing_is_unchanged():
    from mnemo.core.sessions import grants

    for may in ((), grants.parse("push"), grants.parse("pr")):
        assert dispatch._closing_clause(may) == dispatch._closing_clause(may, read_only=False)


def test_dispatch_issue_carries_the_posture_to_the_child(monkeypatch, tmp_path):
    seen = {}

    def fake_spawn(prompt, *, cwd, model=None, lean=True, effort=None, read_only=False):
        seen["prompt"] = prompt
        seen["read_only"] = read_only
        return "abc12345"

    monkeypatch.setattr(dispatch, "spawn_child", fake_spawn)
    monkeypatch.setattr(dispatch, "ensure_worktree", lambda *a, **k: tmp_path)
    monkeypatch.setattr(dispatch.claude_cli, "verify_registered", lambda *a, **k: None)
    monkeypatch.setattr(dispatch.parents, "record", lambda *a, **k: None)
    monkeypatch.setattr(dispatch.grants, "record", lambda *a, **k: None)

    result = dispatch.dispatch_issue(
        361, repo_root=tmp_path,
        fetch=lambda n, **k: dispatch.Issue(number=n, title="the title", body="the body"),
        read_only=True,
    )

    assert seen["read_only"] is True
    assert "Investigate issue #361" in seen["prompt"]
    assert result.read_only is True


def _args(**over):
    base = dict(issues=[361], contract=None, model=None, effort=None, may=None,
                read_only=False, example=False, dry_run=False, full_profile=False)
    base.update(over)
    return argparse.Namespace(**base)


def test_read_only_with_a_grant_is_refused_before_anything_spawns(capsys, monkeypatch):
    def never(*a, **k):
        raise AssertionError("nothing may spawn")

    monkeypatch.setattr(cli_dispatch, "_repo_root", lambda: None)

    code = cli_dispatch.cmd_dispatch(_args(read_only=True, may="pr"))

    assert code == 1
    out = capsys.readouterr().out
    assert "--read-only" in out and "--may" in out


def test_read_only_alone_is_accepted():
    """`--may` defaults to `pr`, so the refusal must key on what was *typed*,
    not on the resolved grant — otherwise `--read-only` can never be used alone.
    """
    from mnemo.core.sessions import grants

    assert cli_dispatch._grant_for(_args(read_only=True)) == ()
    assert cli_dispatch._grant_for(_args()) == grants.parse("pr")
