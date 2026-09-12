"""``mnemo dispatch`` wiring: what the command reports and what it exits with.

The orchestration itself is covered in :mod:`tests.unit.test_dispatch_worktrees`.
What matters here is that a partial failure is *visible* — a dispatcher that
silently skips an issue is worse than one that refuses loudly — and that the
exit code distinguishes "all started" from "some did not".
"""
from __future__ import annotations

import argparse
from pathlib import Path

from mnemo.cli.commands import dispatch as dispatch_cmd
from mnemo.core import dispatch as core


def _args(**kw) -> argparse.Namespace:
    return argparse.Namespace(**{"issues": [197], "dry_run": False, **kw})


def test_reports_each_started_child(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        core, "dispatch_all",
        lambda issues, *, repo_root: [
            core.Dispatched(issue=197, worktree=tmp_path / "p-wt-197", short_id="a1b2c3d4")
        ],
    )

    assert dispatch_cmd.cmd_dispatch(_args()) == 0

    out = capsys.readouterr().out
    assert "#197" in out
    assert "a1b2c3d4" in out
    assert "p-wt-197" in out


def test_reports_a_failure_and_exits_nonzero(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        core, "dispatch_all",
        lambda issues, *, repo_root: [
            core.Dispatched(issue=197, worktree=tmp_path / "p-wt-197", short_id="a1b2c3d4"),
            core.Dispatched(issue=4242, error="issue #4242 not found"),
        ],
    )

    assert dispatch_cmd.cmd_dispatch(_args(issues=[197, 4242])) == 1

    out = capsys.readouterr().out
    assert "a1b2c3d4" in out       # the one that worked is still reported
    assert "not found" in out      # and the one that did not is not silent


def test_points_at_the_queue_rather_than_claude_agents(monkeypatch, capsys, tmp_path: Path) -> None:
    """``claude agents`` requires a TTY and refuses on a pipe; the queue is
    the pipe-safe reader, and the one that shows who is waiting."""
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        core, "dispatch_all",
        lambda issues, *, repo_root: [
            core.Dispatched(issue=197, worktree=tmp_path / "p-wt-197", short_id="a1b2c3d4")
        ],
    )

    dispatch_cmd.cmd_dispatch(_args())

    out = capsys.readouterr().out
    assert "mnemo sessions" in out
    assert "claude agents" not in out


def test_dry_run_spawns_nothing(monkeypatch, capsys, tmp_path: Path) -> None:
    """The plan is printable without creating a worktree or a child."""
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)

    def explode(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("dry run must not dispatch")

    monkeypatch.setattr(core, "dispatch_all", explode)

    assert dispatch_cmd.cmd_dispatch(_args(issues=[197, 198], dry_run=True)) == 0

    out = capsys.readouterr().out
    assert "-wt-197" in out and "fix/issue-197" in out
    assert "-wt-198" in out and "fix/issue-198" in out


def test_refuses_outside_a_git_repo(monkeypatch, capsys) -> None:
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: None)

    assert dispatch_cmd.cmd_dispatch(_args()) == 1
    assert "git" in capsys.readouterr().out.lower()
