"""Discovery: decode project dirs, rank by mtime, filter by project."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from mnemo.core.backfill import discover


@pytest.fixture
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    root = home / ".claude" / "projects"
    root.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return root


def _session(project_dir: Path, name: str, mtime: float) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    p = project_dir / f"{name}.jsonl"
    p.write_text('{"type":"user"}\n', encoding="utf-8")
    os.utime(p, (mtime, mtime))
    return p


def _encode_cwd(cwd: str) -> str:
    """Mirror session_end.py's `_resolve_session_jsonl_path` encoder exactly."""
    encoded = cwd.replace(os.sep, "-")
    if os.altsep:
        encoded = encoded.replace(os.altsep, "-")
    return encoded


def test_decodes_dashed_dir_back_to_cwd():
    encoded = "-Users-xyrlan-github-meunu"
    assert discover.decode_project_dir(encoding=encoded) == "/Users/xyrlan/github/meunu"


def test_decodes_windows_drive_letter_without_leading_slash():
    # session_end.py's encoder replaces os.sep/os.altsep with "-" but leaves
    # the drive letter's colon untouched, so "C:\Users\me\repo" -> this.
    encoded = "C:-Users-me-repo"
    assert discover.decode_project_dir(encoding=encoded) == "C:/Users/me/repo"


def test_finds_transcripts_and_ranks_newest_first(projects_root):
    d = projects_root / "-tmp-repo-alpha"
    _session(d, "old", 1000.0)
    _session(d, "new", 2000.0)

    found = discover.find_transcripts()
    names = [t.path.stem for t in found]
    assert names == ["new", "old"]


def test_limit_takes_the_newest(projects_root):
    d = projects_root / "-tmp-repo-alpha"
    _session(d, "a", 1000.0)
    _session(d, "b", 2000.0)
    _session(d, "c", 3000.0)

    found = discover.find_transcripts(limit=2)
    assert [t.path.stem for t in found] == ["c", "b"]


def test_project_filter_excludes_other_repos(projects_root, monkeypatch):
    monkeypatch.setattr(discover, "_agent_for_cwd", lambda cwd: Path(cwd).name)
    _session(projects_root / "-tmp-alpha", "a1", 1000.0)
    _session(projects_root / "-tmp-beta", "b1", 2000.0)

    found = discover.find_transcripts(project="alpha")
    assert [t.path.stem for t in found] == ["a1"]
    assert all(t.agent == "alpha" for t in found)


def test_resolves_real_repo_through_agent_module(projects_root):
    # Exercise the real `_agent_for_cwd` -> `resolve_canonical_agent` path
    # against an actual matching directory, so the integration (including
    # the `except Exception` wrapper around it) is genuinely covered rather
    # than always falling into `_fallback_agent` or a full monkeypatch.
    #
    # decode_project_dir is a lossy, naive dash-split: it only round-trips
    # exactly when no path segment contains a literal dash. pytest's own
    # tmp_path base commonly does (e.g. "pytest-of-<user>/pytest-3"), so the
    # repo is built under a dedicated, dash-free temp dir via tempfile
    # instead of tmp_path, keeping the round trip honest rather than
    # coincidentally broken by the test harness.
    base = Path(tempfile.mkdtemp(prefix="mnemodiscovertest"))
    try:
        repo = base / "realrepo"
        (repo / ".git").mkdir(parents=True)

        encoded = _encode_cwd(str(repo))
        _session(projects_root / encoded, "s1", 1000.0)

        found = discover.find_transcripts()

        assert [t.path.stem for t in found] == ["s1"]
        assert found[0].agent == "realrepo"
        assert found[0].cwd == str(repo)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_missing_projects_dir_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "nowhere"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "nowhere"))
    assert discover.find_transcripts() == []


def test_non_jsonl_files_are_ignored(projects_root):
    d = projects_root / "-tmp-repo-alpha"
    _session(d, "real", 1000.0)
    d.joinpath("notes.txt").write_text("hi", encoding="utf-8")

    found = discover.find_transcripts()
    assert [t.path.stem for t in found] == ["real"]
