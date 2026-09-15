"""A dispatched child can be traced back to the session that spawned it (#288).

The issue proposed writing the link into the child's worktree. The tests
below that remove the tree before reading are the reason it is not: on
2026-09-15 the tree was already gone for 36 of 43 dispatch children on disk,
while their jobs — and the tokens a consumer wants to sum — were still there.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mnemo.cli.commands import sessions as sessions_cmd
from mnemo.core import dispatch
from mnemo.core.sessions import parents
from mnemo.core.sessions.jobs import Session

PARENT = "870233c6-252f-4b55-ae1f-b675d4557ce8"

BG_STDOUT = (
    "backgrounded · \x1b[36ma1b2c3d4\x1b[39m\n"
    "\x1b[2m  claude attach a1b2c3d4    open in this terminal\x1b[22m\n"
)


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setattr(parents, "_default_vault", lambda: root)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: root)
    return root


# --- the environment --------------------------------------------------------


def test_the_parent_is_the_session_in_the_environment() -> None:
    assert parents.parent_from_env({"CLAUDE_CODE_SESSION_ID": PARENT}) == PARENT


@pytest.mark.parametrize("env", [{}, {"CLAUDE_CODE_SESSION_ID": ""},
                                 {"CLAUDE_CODE_SESSION_ID": "  \n"}],
                         ids=["unset", "empty", "blank"])
def test_a_plain_terminal_has_no_parent(env: dict) -> None:
    assert parents.parent_from_env(env) is None


# --- the log ----------------------------------------------------------------


def test_a_child_dispatched_from_a_session_is_recorded(vault: Path) -> None:
    assert parents.record("a1b2c3d4", env={"CLAUDE_CODE_SESSION_ID": PARENT}) == PARENT
    assert parents.read(vault) == {"a1b2c3d4": PARENT}


def test_a_dispatch_from_a_plain_terminal_writes_nothing(vault: Path) -> None:
    """``null`` is the true answer, and no file is the honest way to give it."""
    assert parents.record("a1b2c3d4", env={}) is None
    assert not parents.log_path(vault).exists()


def test_a_child_without_an_id_is_not_recorded(vault: Path) -> None:
    """Nothing could ever look the link up, so writing it would be noise."""
    assert parents.record("", env={"CLAUDE_CODE_SESSION_ID": PARENT}) is None
    assert not parents.log_path(vault).exists()


def test_an_unwritable_vault_costs_the_link_not_the_dispatch(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")  # .mnemo cannot be created under a file

    assert parents.record(
        "a1b2c3d4", vault_root=blocker, env={"CLAUDE_CODE_SESSION_ID": PARENT}
    ) is None


def test_a_torn_line_costs_one_link_not_the_rest(vault: Path) -> None:
    path = parents.log_path(vault)
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"short_id": "aaaaaaaa", "parent_session": "p1"}\n'
        '{"short_id": "bbbbbb\n'
        '[1, 2]\n'
        '{"short_id": "cccccccc", "parent_session": null}\n'
        '{"short_id": "dddddddd", "parent_session": "p2"}\n',
        encoding="utf-8",
    )

    assert parents.read(vault) == {"aaaaaaaa": "p1", "dddddddd": "p2"}


def test_a_missing_log_reads_as_no_links(tmp_path: Path) -> None:
    assert parents.read(tmp_path) == {}


def test_the_later_dispatch_of_an_id_wins(vault: Path) -> None:
    parents.record("a1b2c3d4", env={"CLAUDE_CODE_SESSION_ID": "old"})
    parents.record("a1b2c3d4", env={"CLAUDE_CODE_SESSION_ID": "new"})

    assert parents.read(vault) == {"a1b2c3d4": "new"}


# --- dispatch writes it -----------------------------------------------------


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    (root / "README.md").write_text("x\n", encoding="utf-8")
    for args in (["init", "-q", "-b", "master"], ["add", "."], ["commit", "-qm", "i"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                       env={**__import__("os").environ, **env})
    return root


def _fake_claude(monkeypatch, jobs: Path, stdout: str = BG_STDOUT) -> None:
    """``claude --bg`` without claude: print the block, register the job."""
    real_run = subprocess.run

    def fake_run(args, **kwargs):
        if args[0] != "claude":
            return real_run(args, **kwargs)
        if "--bg" not in args:  # `claude --version`, asked when an id is missing
            return subprocess.CompletedProcess(args, 0, stdout="2.1.272\n", stderr="")
        entry = jobs / "a1b2c3d4"
        entry.mkdir(parents=True, exist_ok=True)
        (entry / "state.json").write_text(json.dumps({
            "state": "working", "tempo": "active", "cwd": kwargs["cwd"],
            "sessionId": "a1b2c3d4-0000-0000-0000-000000000000",
        }), encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)


def _json_rows(monkeypatch, capsys) -> dict[str, dict]:
    monkeypatch.setattr("mnemo.core.sessions.detector.sweep",
                        lambda sessions, *, vault_root: 0)
    args = argparse.Namespace(json=True, watch=False, **{"all": True})
    assert sessions_cmd.cmd_sessions(args) == 0
    return {row["short_id"]: row for row in json.loads(capsys.readouterr().out)}


@pytest.mark.real_spawn
def test_the_link_outlives_the_worktree(
    repo: Path, vault: Path, tmp_jobs_dir: Path, monkeypatch, capsys
) -> None:
    """The whole design, end to end: dispatch, remove the tree, ask the queue.

    A record under ``<worktree>/.mnemo-child-profile/`` would be gone at the
    last step, which is where a finished child's tokens are finally worth
    summing.
    """
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", PARENT)
    _fake_claude(monkeypatch, tmp_jobs_dir)

    result = dispatch.dispatch_issue(
        288, repo_root=repo,
        fetch=lambda n, repo_root: dispatch.Issue(number=n, title="t", body="b"),
    )
    assert result.short_id == "a1b2c3d4" and result.error is None

    dispatch.remove_worktree(result.worktree, repo_root=repo)
    shutil.rmtree(result.worktree, ignore_errors=True)
    assert not result.worktree.exists()

    rows = _json_rows(monkeypatch, capsys)
    assert rows["a1b2c3d4"]["parent_session"] == PARENT


@pytest.mark.real_spawn
def test_a_child_dispatched_from_a_terminal_reads_null(
    repo: Path, vault: Path, tmp_jobs_dir: Path, monkeypatch, capsys
) -> None:
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    _fake_claude(monkeypatch, tmp_jobs_dir)

    dispatch.dispatch_issue(
        288, repo_root=repo,
        fetch=lambda n, repo_root: dispatch.Issue(number=n, title="t", body="b"),
    )

    rows = _json_rows(monkeypatch, capsys)
    assert "parent_session" in rows["a1b2c3d4"]
    assert rows["a1b2c3d4"]["parent_session"] is None
    assert not parents.log_path(vault).exists()


@pytest.mark.real_spawn
def test_a_child_whose_id_was_not_read_back_is_not_recorded(
    repo: Path, vault: Path, tmp_jobs_dir: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", PARENT)
    _fake_claude(monkeypatch, tmp_jobs_dir, stdout="backgrounded\n")

    result = dispatch.dispatch_issue(
        288, repo_root=repo,
        fetch=lambda n, repo_root: dispatch.Issue(number=n, title="t", body="b"),
    )

    assert result.short_id == "" and result.warning
    assert not parents.log_path(vault).exists()


# --- the queue reads it -----------------------------------------------------


def test_the_queue_stamps_only_the_children_it_knows(vault: Path) -> None:
    parents.record("child001", env={"CLAUDE_CODE_SESSION_ID": PARENT})
    found = [Session(short_id="child001"), Session(short_id="other002")]

    stamped = {s.short_id: s.parent_session for s in parents.stamp(found, vault_root=vault)}

    assert stamped == {"child001": PARENT, "other002": None}


def test_an_unavailable_vault_still_prints_every_row(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "mnemo.core.sessions.jobs.read_sessions",
        lambda root=None, *, cwd=None: [Session(short_id="a")],
    )

    def boom():
        raise RuntimeError("no vault")

    monkeypatch.setattr("mnemo.cli._resolve_vault", boom)
    args = argparse.Namespace(json=True, watch=False, **{"all": True})
    assert sessions_cmd.cmd_sessions(args) == 0

    (row,) = json.loads(capsys.readouterr().out)
    assert row["parent_session"] is None
