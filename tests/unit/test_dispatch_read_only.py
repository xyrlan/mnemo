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
def test_the_prompt_survives_the_variadic_tool_list(monkeypatch, tmp_path):
    """Measured 2026-09-17: `--disallowedTools` takes a space-separated list and
    swallows whatever follows it. Emitted last, it eats the positional prompt and
    the child starts with no instructions at all.
    """
    spawn = _Spawn()
    monkeypatch.setattr(dispatch.subprocess, "run", spawn)
    monkeypatch.setattr(dispatch.claude_cli, "require_short_id", lambda out: "abc12345")

    dispatch.spawn_child("do the thing", cwd=tmp_path, read_only=True)

    assert spawn.args[-1] == "do the thing"
    at = spawn.args.index("--disallowedTools")
    after = spawn.args[at + 1 + len(READ_ONLY_TOOLS)]
    assert after.startswith("-"), f"a flag must follow the tool list, got {after!r}"


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
