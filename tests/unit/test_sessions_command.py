"""``mnemo sessions`` wiring: what scope the command hands the reader.

The reader's own matching is covered in :mod:`tests.unit.test_sessions_jobs`.
What is checked here is only that the command normalizes the cwd it passes —
a worktree or a macOS ``/tmp`` path otherwise reaches the reader in a form
that matches nothing and the queue reports an empty repo.
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
