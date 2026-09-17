"""The read-only posture: a child that investigates and cannot edit files."""
from __future__ import annotations

from pathlib import Path

import pytest

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
