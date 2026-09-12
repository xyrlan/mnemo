"""``mnemo sessions`` wiring: what scope the command hands the reader.

The reader's own matching is covered in :mod:`tests.unit.test_sessions_jobs`.
What is checked here is only that the command normalizes the cwd it passes —
a worktree or a macOS ``/tmp`` path otherwise reaches the reader in a form
that matches nothing and the queue reports an empty repo — and that the
detector's sweep rides the read.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from mnemo.cli.commands import sessions as sessions_cmd


def _run(monkeypatch, cwd: str, **flags) -> list[str | None]:
    seen: list[str | None] = []

    def fake_read_sessions(root=None, *, cwd=None):
        seen.append(cwd)
        return []

    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions", fake_read_sessions)
    monkeypatch.setattr("os.getcwd", lambda: cwd)

    args = argparse.Namespace(json=False, watch=False, **{"all": False, **flags})
    assert sessions_cmd.cmd_sessions(args) == 0
    return seen


def test_passes_the_normalized_cwd_to_the_reader(monkeypatch, tmp_path: Path) -> None:
    real = tmp_path / "real-repo"
    real.mkdir()
    link = tmp_path / "linked-repo"
    link.symlink_to(real)

    seen = _run(monkeypatch, str(link) + "/")

    assert seen == [str(real.resolve())]


def test_all_passes_no_scope(monkeypatch, tmp_path: Path) -> None:
    seen = _run(monkeypatch, str(tmp_path), all=True)

    assert seen == [None]


def test_reading_the_queue_sweeps_for_unblocks(monkeypatch, tmp_path: Path) -> None:
    """The detector rides the command: nothing else runs it."""
    vault = tmp_path / "vault"
    found = [object()]
    swept: list[tuple[object, Path]] = []

    monkeypatch.setattr(
        "mnemo.core.sessions.jobs.read_sessions",
        lambda root=None, *, cwd=None: found,
    )
    monkeypatch.setattr("mnemo.core.sessions.render.render_queue", lambda s: "queue")
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    monkeypatch.setattr(
        "mnemo.core.sessions.detector.sweep",
        lambda sessions, *, vault_root: swept.append((sessions, vault_root)) or 0,
    )

    args = argparse.Namespace(json=False, watch=False, **{"all": True})
    assert sessions_cmd.cmd_sessions(args) == 0

    assert swept == [(found, vault)]


def test_the_json_path_sweeps_too(monkeypatch, tmp_path: Path) -> None:
    """``--json`` shares ``_read`` with the other two branches; nothing else
    pins that, and a machine-read queue is as good a sweep trigger as a human
    one."""
    vault = tmp_path / "vault"
    swept: list[tuple[object, Path]] = []

    monkeypatch.setattr(
        "mnemo.core.sessions.jobs.read_sessions",
        lambda root=None, *, cwd=None: [],
    )
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    monkeypatch.setattr(
        "mnemo.core.sessions.detector.sweep",
        lambda sessions, *, vault_root: swept.append((sessions, vault_root)) or 0,
    )

    args = argparse.Namespace(json=True, watch=False, **{"all": True})
    assert sessions_cmd.cmd_sessions(args) == 0

    assert swept == [([], vault)]


def test_an_unavailable_vault_still_prints_the_queue(monkeypatch, capsys) -> None:
    """A queue that refused to print because the vault moved would be useless."""
    def boom() -> Path:
        raise RuntimeError("vault is gone")

    monkeypatch.setattr(
        "mnemo.core.sessions.jobs.read_sessions",
        lambda root=None, *, cwd=None: [],
    )
    monkeypatch.setattr("mnemo.core.sessions.render.render_queue", lambda s: "the queue")
    monkeypatch.setattr("mnemo.cli._resolve_vault", boom)

    args = argparse.Namespace(json=False, watch=False, **{"all": True})
    assert sessions_cmd.cmd_sessions(args) == 0

    assert "the queue" in capsys.readouterr().out
