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
        lambda issues, *, repo_root, model=None, lean=True, may=(), effort=None: [
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
        lambda issues, *, repo_root, model=None, lean=True, may=(), effort=None: [
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
        lambda issues, *, repo_root, model=None, lean=True, may=(), effort=None: [
            core.Dispatched(issue=197, worktree=tmp_path / "p-wt-197", short_id="a1b2c3d4")
        ],
    )

    dispatch_cmd.cmd_dispatch(_args())

    out = capsys.readouterr().out
    assert "mnemo sessions" in out
    assert "claude agents" not in out


def test_the_attach_hint_names_the_first_child_that_has_an_id(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    """The hint must address a child that can actually be attached.

    ``short_id`` is ``""`` when the id could not be read back out of the spawn
    output. Taking ``started[0]`` blindly then prints ``claude attach `` with
    nothing after it — a command that cannot work, which is the #211 failure
    in a quieter form.
    """
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        core, "dispatch_all",
        lambda issues, *, repo_root, model=None, lean=True, may=(), effort=None: [
            core.Dispatched(issue=197, worktree=tmp_path / "p-wt-197", short_id=""),
            core.Dispatched(issue=198, worktree=tmp_path / "p-wt-198", short_id="a1b2c3d4"),
        ],
    )

    assert dispatch_cmd.cmd_dispatch(_args(issues=[197, 198])) == 0

    out = capsys.readouterr().out
    assert "claude attach a1b2c3d4" in out
    assert "claude attach \n" not in out


def test_no_attach_hint_when_no_child_reported_an_id(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    """With no id anywhere, the queue is the only honest next step.

    The children are running regardless — ``mnemo sessions`` reads their
    ``state.json`` and finds them — so the report says so instead of printing
    a truncated command.
    """
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        core, "dispatch_all",
        lambda issues, *, repo_root, model=None, lean=True, may=(), effort=None: [
            core.Dispatched(issue=197, worktree=tmp_path / "p-wt-197", short_id="")
        ],
    )

    assert dispatch_cmd.cmd_dispatch(_args()) == 0

    out = capsys.readouterr().out
    assert "mnemo sessions" in out       # the queue still finds them
    assert "claude attach" not in out    # but never a command that cannot work


def test_a_warning_is_printed_under_its_row(monkeypatch, capsys, tmp_path: Path) -> None:
    """A child that started while a ``claude`` CLI assumption broke is loud (#235).

    The row is still a started row — exit 0, the tree is kept — but the
    warning names the assumption under it and the id column shows a visible
    placeholder rather than the blank that read as "missing" before.
    """
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        core, "dispatch_all",
        lambda issues, *, repo_root, model=None, lean=True, may=(), effort=None: [
            core.Dispatched(
                issue=197, worktree=tmp_path / "p-wt-197", short_id="",
                warning="claude CLI assumption `bg-prints-short-id` did not hold (installed claude 9.9.9)",
            )
        ],
    )

    assert dispatch_cmd.cmd_dispatch(_args()) == 0

    out = capsys.readouterr().out
    assert "WARNING: claude CLI assumption `bg-prints-short-id`" in out
    assert "installed claude 9.9.9" in out
    assert "#197  ????????  " in out
    assert "pytest -m live_claude" in out
    assert "claude attach" not in out


def test_no_warning_line_when_nothing_broke(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        core, "dispatch_all",
        lambda issues, *, repo_root, model=None, lean=True, may=(), effort=None: [
            core.Dispatched(issue=197, worktree=tmp_path / "p-wt-197", short_id="a1b2c3d4")
        ],
    )
    assert dispatch_cmd.cmd_dispatch(_args()) == 0
    out = capsys.readouterr().out
    assert "WARNING" not in out
    assert "live_claude" not in out


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


# --- --contract: the same command, named by a decomposition instead ---------

VALID_CONTRACT = """\
---
feature: contract-dispatch
created: 2026-09-12
verdict: parallel
---

## parser
- **files:** src/mnemo/core/contracts.py
- **exposes:** `parse_contract(path) -> Contract`
- **consumes:** nothing
"""


def test_contract_flag_parses() -> None:
    from mnemo.cli.parser import _build_parser

    args = _build_parser().parse_args(["dispatch", "--contract", "c.md"])
    assert args.contract == "c.md"
    assert not args.issues


def test_contract_and_issues_are_refused_together(monkeypatch, tmp_path, capsys) -> None:
    """Enforced in the command, not argparse: a variadic positional cannot
    share a mutually exclusive group with a flag."""
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    args = argparse.Namespace(issues=[193], contract="c.md", dry_run=False)
    assert dispatch_cmd.cmd_dispatch(args) == 1
    assert "both" in capsys.readouterr().out.lower()


def test_neither_issues_nor_contract_is_refused(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    args = argparse.Namespace(issues=[], contract=None, dry_run=False)
    assert dispatch_cmd.cmd_dispatch(args) == 1
    assert "issue" in capsys.readouterr().out.lower()


def test_dry_run_prints_pieces_without_spawning(monkeypatch, tmp_path, capsys) -> None:
    """The paths are pure functions of the contract, so the plan is checkable."""
    contract = tmp_path / "c.md"
    contract.write_text(VALID_CONTRACT, encoding="utf-8")
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path / "proj")
    monkeypatch.setattr(
        core, "spawn_child",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawned")),
    )
    args = argparse.Namespace(issues=[], contract=str(contract), dry_run=True)
    assert dispatch_cmd.cmd_dispatch(args) == 0
    out = capsys.readouterr().out
    assert "proj-wt-c-parser" in out
    assert "feat/contract-dispatch/parser" in out


def test_unreadable_contract_is_reported_not_raised(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    args = argparse.Namespace(
        issues=[], contract=str(tmp_path / "missing.md"), dry_run=False
    )
    assert dispatch_cmd.cmd_dispatch(args) == 1
    assert "contract" in capsys.readouterr().out.lower()


def test_a_sequential_contract_is_refused_without_spawning(
    monkeypatch, tmp_path, capsys
) -> None:
    """``sequential`` is the decomposition saying the work does not divide."""
    contract = tmp_path / "c.md"
    contract.write_text(
        VALID_CONTRACT.replace("verdict: parallel", "verdict: sequential"),
        encoding="utf-8",
    )
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path / "proj")
    monkeypatch.setattr(
        core, "spawn_child",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawned")),
    )
    args = argparse.Namespace(issues=[], contract=str(contract), dry_run=False)
    assert dispatch_cmd.cmd_dispatch(args) == 1
    assert "sequential" in capsys.readouterr().out.lower()


def _one_child(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        core, "dispatch_all",
        lambda issues, *, repo_root, model=None, lean=True, may=(), effort=None: [
            core.Dispatched(issue=197, worktree=tmp_path / "p-wt-197", short_id="a1b2c3d4")
        ],
    )


def test_inside_a_session_the_footer_tells_the_model_not_to_promise_to_watch(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    """The model reads this output through its Bash tool and repeated the
    ``queue:`` line as its own plan — "Acompanho com `mnemo sessions`… te
    aviso quando terminarem" — with nothing that would wake it (#306)."""
    _one_child(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "11111111-2222-3333-4444-555555555555")

    assert dispatch_cmd.cmd_dispatch(_args()) == 0

    out = capsys.readouterr().out
    assert "a1b2c3d4" in out                         # the ids are still reported
    assert "nothing" in out and "tells this session" in out
    assert "do not poll them or promise to watch" in out
    assert out.rstrip().endswith("The queue and attach lines above are for the maintainer.")


def test_from_a_plain_terminal_the_footer_is_unchanged(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    """The maintainer at a terminal is the reader the footer was written for."""
    _one_child(monkeypatch, tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

    assert dispatch_cmd.cmd_dispatch(_args()) == 0

    out = capsys.readouterr().out
    assert "  queue:  mnemo sessions" in out
    assert "claude attach a1b2c3d4" in out
    assert "tells this session" not in out


def test_a_blank_session_variable_is_a_plain_terminal(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    """Same predicate as the parent link (``parents.parent_from_env``): an
    empty export is not a session, so the two can never disagree about who
    ran dispatch."""
    _one_child(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "  ")

    dispatch_cmd.cmd_dispatch(_args())

    assert "tells this session" not in capsys.readouterr().out


def test_no_note_when_nothing_started(monkeypatch, capsys, tmp_path: Path) -> None:
    """With no child running there is nothing to promise about."""
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        core, "dispatch_all",
        lambda issues, *, repo_root, model=None, lean=True, may=(), effort=None: [
            core.Dispatched(issue=4242, error="issue #4242 not found"),
        ],
    )
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "11111111-2222-3333-4444-555555555555")

    assert dispatch_cmd.cmd_dispatch(_args(issues=[4242])) == 1

    assert "tells this session" not in capsys.readouterr().out
