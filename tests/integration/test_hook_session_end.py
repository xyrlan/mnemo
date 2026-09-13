# tests/integration/test_hook_session_end.py
from __future__ import annotations

import io
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from mnemo.core import session
from mnemo.hooks import session_end


@pytest.fixture
def hook_env(tmp_vault: Path, tmp_home: Path, tmp_tempdir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    return tmp_vault


def test_session_end_logs_and_clears_cache(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    session.save("S1", {"name": "myrepo", "repo_root": "/x", "has_git": True})
    payload = json.dumps({"session_id": "S1", "reason": "exit"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    rc = session_end.main()
    assert rc == 0
    log = (hook_env / "bots" / "myrepo" / "logs" / f"{date.today().isoformat()}.md").read_text()
    assert "🔴 session ended (exit)" in log
    assert session.load("S1") is None


def test_session_end_falls_back_when_cache_missing(hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "r3"
    (repo / ".git").mkdir(parents=True)
    payload = json.dumps({"session_id": "missing", "reason": "compact", "cwd": str(repo)})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    rc = session_end.main()
    assert rc == 0
    log = (hook_env / "bots" / "r3" / "logs" / f"{date.today().isoformat()}.md").read_text()
    assert "🔴 session ended (compact)" in log


def _make_worktree(tmp_path: Path, *, repo_name: str) -> Path:
    """Build a main repo + one worktree that resolves canonically to *repo_name*."""
    main_repo = tmp_path / repo_name
    main_repo.mkdir()
    git_dir = main_repo / ".git"
    git_dir.mkdir()
    wt_dir = git_dir / "worktrees" / "feature-x"
    wt_dir.mkdir(parents=True)
    (wt_dir / "commondir").write_text("../..\n")
    worktree = tmp_path / f"{repo_name}-feature-x"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_dir}\n")
    return worktree


def test_session_end_cache_miss_logs_under_canonical_project(
    hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """#225: the cache-miss fallback must not open an orphan worktree namespace.

    Pre-fix this wrote `bots/wtrepo-feature-x/logs/`, one orphan dir per
    dispatched worktree, while injection used the canonical name.
    """
    worktree = _make_worktree(tmp_path, repo_name="wtrepo")
    payload = json.dumps(
        {"session_id": "wt-miss", "reason": "exit", "cwd": str(worktree)}
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    rc = session_end.main()
    assert rc == 0
    today = f"{date.today().isoformat()}.md"
    assert (hook_env / "bots" / "wtrepo" / "logs" / today).exists()
    assert not (hook_env / "bots" / "wtrepo-feature-x").exists()
