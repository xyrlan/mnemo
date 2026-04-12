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
