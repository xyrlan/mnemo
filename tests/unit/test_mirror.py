# tests/unit/test_mirror.py
from __future__ import annotations

import os
from pathlib import Path

import pytest

from mnemo.core import mirror


def _make_claude_project(home: Path, encoded_name: str, files: dict[str, str]) -> Path:
    project_dir = home / ".claude" / "projects" / encoded_name / "memory"
    project_dir.mkdir(parents=True)
    for rel, content in files.items():
        p = project_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return project_dir


def test_extracts_agent_name_from_encoded_dir():
    assert mirror._agent_from_project_dir("-home-user-github-sg-imports") == "sg-imports"
    assert mirror._agent_from_project_dir("-Users-foo-Code-app") == "app"
    assert mirror._agent_from_project_dir("-") == "root"
    assert mirror._agent_from_project_dir("") == "root"


def test_mirror_copies_memory_files(tmp_home: Path, tmp_vault: Path):
    _make_claude_project(tmp_home, "-home-x-myrepo", {
        "feedback.md": "# feedback content",
        "user_role.md": "# user role",
    })
    cfg = {"vaultRoot": str(tmp_vault)}
    mirror.mirror_all(cfg)
    target_dir = tmp_vault / "bots" / "myrepo" / "memory"
    assert (target_dir / "feedback.md").read_text() == "# feedback content"
    assert (target_dir / "user_role.md").read_text() == "# user role"


def test_mirror_never_deletes_user_notes(tmp_home: Path, tmp_vault: Path):
    _make_claude_project(tmp_home, "-home-x-myrepo", {"a.md": "from claude"})
    cfg = {"vaultRoot": str(tmp_vault)}
    target_dir = tmp_vault / "bots" / "myrepo" / "memory"
    target_dir.mkdir(parents=True)
    (target_dir / "user_note.md").write_text("user wrote this")
    mirror.mirror_all(cfg)
    assert (target_dir / "user_note.md").exists()
    assert (target_dir / "a.md").exists()


def test_mirror_skips_when_no_claude_projects(tmp_home: Path, tmp_vault: Path):
    cfg = {"vaultRoot": str(tmp_vault)}
    mirror.mirror_all(cfg)  # must not raise
    assert (tmp_vault / "bots").exists()


def test_python_fallback_when_rsync_missing(tmp_home: Path, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch):
    """Critical test from spec § 10.3: test_missing_rsync_fallback."""
    _make_claude_project(tmp_home, "-home-x-myrepo", {
        "deep/nested/file.md": "deep content",
        "top.md": "top content",
    })
    monkeypatch.setattr(mirror, "_has_rsync", lambda: False)
    cfg = {"vaultRoot": str(tmp_vault)}
    mirror.mirror_all(cfg)
    target = tmp_vault / "bots" / "myrepo" / "memory"
    assert (target / "top.md").read_text() == "top content"
    assert (target / "deep" / "nested" / "file.md").read_text() == "deep content"


def test_mirror_lock_prevents_concurrent_runs(tmp_home: Path, tmp_vault: Path):
    _make_claude_project(tmp_home, "-home-x-myrepo", {"a.md": "x"})
    cfg = {"vaultRoot": str(tmp_vault)}
    # Hold the lock manually
    lock = tmp_vault / ".mirror.lock"
    lock.mkdir()
    os.utime(lock, None)  # fresh
    try:
        mirror.mirror_all(cfg)
    finally:
        lock.rmdir()
    # Second mirror noop'd, so target memory dir should not exist.
    assert not (tmp_vault / "bots" / "myrepo" / "memory" / "a.md").exists()


def test_decode_resolves_to_real_git_root(tmp_path: Path):
    """When the encoded path corresponds to a real filesystem path with .git, use git-root basename.

    Regression: previously, the encoded path was parsed via a brittle 'skip first 3 components'
    heuristic that produced 'refactor-sg-imports' for /home/xyrlan/github/refactor/sg-imports
    when the real git-root basename is 'sg-imports'.
    """
    repo = tmp_path / "github" / "refactor" / "sg-imports"
    (repo / ".git").mkdir(parents=True)
    encoded = "-" + str(repo).lstrip("/").replace("/", "-")
    assert mirror._agent_from_project_dir(encoded) == "sg-imports"


def test_decode_handles_repo_with_internal_dashes(tmp_path: Path):
    """Repo names containing dashes should be recovered intact via filesystem decoding."""
    repo = tmp_path / "code" / "my-cool-project"
    (repo / ".git").mkdir(parents=True)
    encoded = "-" + str(repo).lstrip("/").replace("/", "-")
    assert mirror._agent_from_project_dir(encoded) == "my-cool-project"


def test_decode_falls_back_to_heuristic_when_path_missing():
    """When the encoded path no longer exists on disk, fall back to the existing heuristic."""
    # /no/such/path/here-bogus does not exist on this machine
    encoded = "-no-such-path-here-bogus"
    result = mirror._agent_from_project_dir(encoded)
    # Either the heuristic produces "here-bogus" (skip 3 → ['here','bogus']) or "bogus" (last segment).
    # Both are acceptable as long as it does not raise.
    assert result and isinstance(result, str)


def test_decode_walks_up_to_git_root_when_subdir_passed(tmp_path: Path):
    """If the encoded path points inside a subdir of a git repo, walk up to the git root."""
    repo = tmp_path / "work" / "myrepo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "src" / "deep" / "module"
    sub.mkdir(parents=True)
    encoded = "-" + str(sub).lstrip("/").replace("/", "-")
    assert mirror._agent_from_project_dir(encoded) == "myrepo"
