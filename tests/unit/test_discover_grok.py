"""Discovery finds Grok sessions in every home, filed under their repo's agent."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mnemo.core.backfill import discover
from tests.unit._grok_history import write_session

REPO = "/Users/you/github/app"
WORKTREE = "/Users/you/github/app-wt-c-parser"
OTHER = "/Users/you/github/other"

_AGENTS = {REPO: "app", WORKTREE: "app", OTHER: "other"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    for name in ("GROK_HOME", "GROK_SESSION_ID", "MNEMO_AGENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(discover, "agent_for_cwd", lambda cwd: _AGENTS.get(cwd, Path(cwd).name))


def _touch(path: Path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))


def _claude(home: Path, cwd: str, session_id: str, mtime: float) -> Path:
    jsonl = home / ".claude" / "projects" / discover.encode_cwd(cwd) / f"{session_id}.jsonl"
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    jsonl.write_text(json.dumps({"type": "user", "cwd": cwd}) + "\n", encoding="utf-8")
    _touch(jsonl, mtime)
    return jsonl


def test_sessions_of_every_home_are_found_for_the_repo_and_its_worktrees(tmp_home: Path):
    a = write_session(tmp_home / ".grok", cwd=REPO, session_id="01a1-0000-aaaa0001")
    b = write_session(tmp_home / ".grok-work", cwd=WORKTREE, session_id="01a1-0000-bbbb0002")
    write_session(tmp_home / ".grok", cwd=OTHER, session_id="01a1-0000-cccc0003")
    _touch(a, 1_000)
    _touch(b, 2_000)

    found = discover.find_grok_transcripts(project="app")

    assert [t.path for t in found] == [b, a]
    assert [(t.agent, t.cwd) for t in found] == [("app", WORKTREE), ("app", REPO)]


def test_grok_home_env_names_an_extra_home(tmp_home: Path, tmp_path: Path, monkeypatch):
    home = tmp_path / "elsewhere" / "grok"
    history = write_session(home, cwd=REPO)
    monkeypatch.setenv("GROK_HOME", str(home))

    assert [t.path for t in discover.find_grok_transcripts(project="app")] == [history]


def test_a_hashed_cwd_directory_is_read_from_its_cwd_file(tmp_home: Path):
    history = write_session(tmp_home / ".grok", cwd=REPO, summary_cwd=False, dir_name="app-0123456789abcdef")
    (history.parent.parent / ".cwd").write_text(REPO + "\n", encoding="utf-8")

    found = discover.find_grok_transcripts(project="app")

    assert [(t.path, t.cwd) for t in found] == [(history, REPO)]


def test_a_session_without_history_or_cwd_is_skipped(tmp_home: Path):
    empty = tmp_home / ".grok" / "sessions" / "%2FUsers%2Fyou%2Fgithub%2Fapp" / "01a1-0000-dddd0004"
    empty.mkdir(parents=True)
    write_session(tmp_home / ".grok", cwd=REPO, summary_cwd=False, dir_name="not-a-path")

    assert discover.find_grok_transcripts() == []


def test_find_transcripts_stays_claude_only_by_default(tmp_home: Path):
    claude = _claude(tmp_home, REPO, "c1", 1_000)
    grok = write_session(tmp_home / ".grok", cwd=REPO)
    _touch(grok, 5_000)

    assert [t.path for t in discover.find_transcripts(project="app")] == [claude]
    assert [t.path for t in discover.find_transcripts(project="app", grok=True)] == [grok, claude]


def test_grok_and_claude_share_one_sort_and_one_limit(tmp_home: Path):
    old = _claude(tmp_home, REPO, "c-old", 1_000)
    new = _claude(tmp_home, REPO, "c-new", 3_000)
    mid = write_session(tmp_home / ".grok", cwd=REPO)
    _touch(mid, 2_000)

    found = discover.find_transcripts(project="app", grok=True, limit=2)

    assert [t.path for t in found] == [new, mid]
    assert old not in [t.path for t in found]


def test_no_grok_home_finds_nothing(tmp_home: Path):
    assert discover.find_grok_transcripts() == []
    assert discover.find_transcripts(grok=True) == []
