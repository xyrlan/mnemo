"""An unblock marker from a deleted worktree resolves to its transcript (#301).

Measured on the real vault (2026-09-15): every pending marker carried a
dispatch-tree ``cwd`` (``~/github/mnemo-wt-200``), every transcript was still
on disk, and ``learn`` found none of them — 27 then, 40 by the fix, 0
consumed. Two halves disagreed about the project: ``learn`` named it from the
marker's ``cwd`` (a gone tree, so its basename), and discovery named each
project directory by decoding a dashed name that never existed.

These run the real chain — ``consume`` -> ``learn`` -> ``newest_transcript``
-> ``find_transcripts`` — with only the two LLM stages stubbed, because every
test that stubbed ``learn`` itself passed while the chain matched nothing.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mnemo.core.sessions import detector, unblocks

SID = "9293fe7b-a31b-4819-8a8d-d6c660a959c8"


@pytest.fixture
def projects(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "claude" / "projects"
    root.mkdir(parents=True)
    monkeypatch.setattr("mnemo.core.backfill.discover.projects_root", lambda: root)
    return root


@pytest.fixture
def stages(monkeypatch):
    """Stub the briefing and extraction; record what each was handed."""
    seen: dict = {}

    def _brief(path, project, cfg, **kw):
        seen["transcript"], seen["project"] = Path(path), project
        return None

    def _extract(cfg, *, only):
        seen["only"] = only
        return type("Summary", (), {"demoted_unverified": 0})()

    monkeypatch.setattr("mnemo.core.briefing.generate_session_briefing", _brief)
    monkeypatch.setattr("mnemo.core.extract.run_extraction", _extract)
    return seen


def _transcript(projects: Path, cwd: Path) -> Path:
    """Where Claude Code puts it, and the shape it writes: cwd on an event."""
    project_dir = projects / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    project_dir.mkdir(parents=True)
    path = project_dir / f"{SID}.jsonl"
    events = [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": SID},
        {"type": "user", "cwd": str(cwd), "sessionId": SID,
         "message": {"role": "user", "content": "yes, go ahead"}},
    ]
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return path


def _marker(vault: Path, *, cwd: Path, transcript: Path) -> None:
    state = {"seen": {SID[:8]: {"last_tempo": "active", "unblocks": [{
        "at": "2026-09-12T22:49:08+00:00",
        "answer": "yes, go ahead",
        "needs": "should I push?",
        "session_id": SID,
        "link_scan_path": str(transcript),
        "cwd": str(cwd),
        "extracted": False,
    }]}}}
    path = vault / ".mnemo" / detector.STATE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    return vault


def test_a_marker_from_a_deleted_worktree_is_consumed(tmp_path, projects, stages) -> None:
    repo = tmp_path / "github" / "repo"
    (repo / ".git").mkdir(parents=True)
    gone = tmp_path / "github" / "repo-wt-200"  # delivered, tree removed
    transcript = _transcript(projects, gone)
    vault = _vault(tmp_path)
    _marker(vault, cwd=gone, transcript=transcript)

    report = unblocks.consume({"vaultRoot": str(vault)}, vault_root=vault)

    assert report.errors == []
    assert (report.consumed, report.failed, report.retired) == (1, 0, 0)
    assert stages["transcript"] == transcript
    # Filed under the repo, not an orphan `repo-wt-200` namespace (#225).
    assert stages["project"] == "repo"
    assert detector.pending_unblocks(vault_root=vault) == []


def test_a_marker_from_a_live_dashed_worktree_is_consumed(tmp_path, projects, stages) -> None:
    repo = tmp_path / "github" / "repo"
    (repo / ".git").mkdir(parents=True)
    tree = tmp_path / "github" / "repo-wt-c-briefing-query"
    gitdir = repo / ".git" / "worktrees" / tree.name
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    tree.mkdir(parents=True)
    (tree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    transcript = _transcript(projects, tree)
    vault = _vault(tmp_path)
    _marker(vault, cwd=tree, transcript=transcript)

    report = unblocks.consume({"vaultRoot": str(vault)}, vault_root=vault)

    assert (report.consumed, report.failed) == (1, 0)
    assert (stages["transcript"], stages["project"]) == (transcript, "repo")
