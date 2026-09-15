"""Finished children whose worktree is gone leave the queue (#292).

A dispatch child's tree is removed once its PR merges, but its job record
stays until ``claude rm``. On 2026-09-15 that was 48 records and 14 PRONTAS
rows for work merged days earlier. The rows are hidden by default, counted in
a footer, restored by ``--stale``, and named with their cleanup in doctor.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from mnemo.cli.commands import sessions as sessions_cmd
from mnemo.cli.commands.doctor_checks import misc as doctor_misc
from mnemo.core.sessions.jobs import Session


@pytest.fixture
def trees(tmp_path: Path) -> tuple[str, str]:
    """(a worktree that exists, one that was removed)."""
    alive = tmp_path / "repo-wt-1"
    alive.mkdir()
    return str(alive), str(tmp_path / "repo-wt-2")


# --- the rule ---------------------------------------------------------------

@pytest.mark.parametrize("state", ["done", "stopped"])
def test_a_finished_session_in_a_removed_tree_is_stale(trees, state) -> None:
    _, gone = trees
    assert Session(short_id="a", state=state, tempo="idle", cwd=gone).is_stale is True


def test_a_finished_session_whose_tree_exists_is_not(trees) -> None:
    alive, _ = trees
    assert Session(short_id="a", state="done", tempo="idle", cwd=alive).is_stale is False


@pytest.mark.parametrize("state", ["working", "blocked", None])
def test_an_unfinished_session_is_never_stale(trees, state) -> None:
    _, gone = trees
    assert Session(short_id="a", state=state, tempo="active", cwd=gone).is_stale is False


def test_a_blocked_session_is_never_hidden_even_when_stopped(trees) -> None:
    # Whatever the phase says, a question nobody answered must stay visible.
    _, gone = trees
    s = Session(short_id="a", state="stopped", tempo="blocked", cwd=gone)
    assert s.is_stale is False


def test_no_recorded_cwd_proves_nothing() -> None:
    assert Session(short_id="a", state="done", tempo="idle").is_stale is False


# --- the queue --------------------------------------------------------------

def _serve(monkeypatch, found, swept=None) -> None:
    monkeypatch.setattr(
        "mnemo.core.sessions.jobs.read_sessions",
        lambda root=None, *, cwd=None: found,
    )
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: Path("/vault"))
    monkeypatch.setattr(
        "mnemo.core.sessions.detector.sweep",
        lambda sessions, *, vault_root: (swept.append(list(sessions)) if swept is not None else None) or 0,
    )


def _args(**flags) -> argparse.Namespace:
    return argparse.Namespace(**{"json": False, "watch": False, "all": True, **flags})


def _queue(trees) -> list[Session]:
    alive, gone = trees
    return [
        Session(short_id="merged01", state="done", tempo="idle", cwd=gone, name="merged"),
        Session(short_id="ready002", state="done", tempo="idle", cwd=alive, name="ready"),
        Session(short_id="asking03", state="blocked", tempo="blocked", cwd=gone, name="asking"),
    ]


def test_the_text_queue_hides_them_and_says_how_many(monkeypatch, capsys, trees) -> None:
    _serve(monkeypatch, _queue(trees))

    assert sessions_cmd.cmd_sessions(_args()) == 0

    out = capsys.readouterr().out
    assert "merged01" not in out
    assert "ready002" in out
    assert "asking03" in out
    assert "1 prontas com a árvore já removida" in out
    assert "--stale" in out


def test_stale_brings_them_back(monkeypatch, capsys, trees) -> None:
    _serve(monkeypatch, _queue(trees))

    assert sessions_cmd.cmd_sessions(_args(stale=True)) == 0

    out = capsys.readouterr().out
    assert "merged01" in out
    assert "--stale" not in out


def test_nothing_hidden_prints_no_footer(monkeypatch, capsys, trees) -> None:
    alive, _ = trees
    _serve(monkeypatch, [Session(short_id="ready002", state="done", tempo="idle", cwd=alive)])

    assert sessions_cmd.cmd_sessions(_args()) == 0

    assert "--stale" not in capsys.readouterr().out


def test_json_drops_them_unless_asked(monkeypatch, capsys, trees) -> None:
    _serve(monkeypatch, _queue(trees))

    assert sessions_cmd.cmd_sessions(_args(json=True)) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["short_id"] for r in rows] == ["ready002", "asking03"]

    assert sessions_cmd.cmd_sessions(_args(json=True, stale=True)) == 0
    rows = {r["short_id"]: r for r in json.loads(capsys.readouterr().out)}
    assert set(rows) == {"merged01", "ready002", "asking03"}
    assert rows["merged01"]["is_stale"] is True
    assert rows["ready002"]["is_stale"] is False


def test_the_sweep_still_sees_them(monkeypatch, capsys, trees) -> None:
    # A child's last answer can sit unswept in its transcript after the tree
    # is removed; hiding the row must not cost the unblock marker.
    swept: list = []
    _serve(monkeypatch, _queue(trees), swept)

    assert sessions_cmd.cmd_sessions(_args()) == 0

    assert [s.short_id for s in swept[0]] == ["merged01", "ready002", "asking03"]


def test_the_elsewhere_count_ignores_them(monkeypatch, capsys, trees) -> None:
    # #281's pointer to `--all` must not send the user after rows it would hide.
    _, gone = trees
    stale = Session(short_id="merged01", state="done", tempo="idle", cwd=gone)
    monkeypatch.setattr(
        "mnemo.core.sessions.jobs.read_sessions",
        lambda root=None, *, cwd=None: [] if cwd is not None else [stale],
    )
    monkeypatch.setattr("os.getcwd", lambda: "/repo")
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: Path("/vault"))
    monkeypatch.setattr("mnemo.core.sessions.detector.sweep", lambda sessions, *, vault_root: 0)

    assert sessions_cmd.cmd_sessions(_args(all=False)) == 0

    assert "outros diretórios" not in capsys.readouterr().out


def test_the_parser_accepts_stale() -> None:
    from mnemo.cli.parser import _build_parser as build_parser

    assert build_parser().parse_args(["sessions", "--all", "--stale"]).stale is True
    assert build_parser().parse_args(["sessions"]).stale is False


# --- doctor -----------------------------------------------------------------

def _job(root: Path, short_id: str, **fields) -> None:
    d = root / short_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(fields), encoding="utf-8")


def test_doctor_names_every_stale_job_with_its_cleanup(tmp_path: Path, capsys, trees) -> None:
    alive, gone = trees
    root = tmp_path / "jobs"
    root.mkdir()
    ids = [f"stale{n:03d}" for n in range(7)]  # more than any display cap
    for short_id in ids:
        _job(root, short_id, state="done", tempo="idle", cwd=gone)
    _job(root, "keepme01", state="done", tempo="idle", cwd=alive)
    _job(root, "asking03", state="blocked", tempo="blocked", cwd=gone)

    assert doctor_misc._doctor_check_background_sessions(jobs_root=root) is True

    out = capsys.readouterr().out
    assert "7 finished sessions whose worktree is gone" in out
    command = next(line for line in out.splitlines() if "claude rm" in line)
    for short_id in ids:
        assert short_id in command
    assert "keepme01" not in command
    assert "asking03" not in command


def test_doctor_is_silent_about_stale_when_there_are_none(tmp_path: Path, capsys, trees) -> None:
    alive, _ = trees
    root = tmp_path / "jobs"
    root.mkdir()
    _job(root, "keepme01", state="done", tempo="idle", cwd=alive)

    assert doctor_misc._doctor_check_background_sessions(jobs_root=root) is True

    assert "claude rm" not in capsys.readouterr().out


def test_the_one_liner_speaks_each_shell() -> None:
    assert doctor_misc._claude_rm_each(["a1", "b2"], windows=False) == \
        'for id in a1 b2; do claude rm "$id"; done'
    assert doctor_misc._claude_rm_each(["a1", "b2"], windows=True) == \
        "foreach ($id in 'a1','b2') { claude rm $id }"
