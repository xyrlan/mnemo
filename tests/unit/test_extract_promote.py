"""Unit tests for core/extract/promote.py — project 1:1 promotion."""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo.core.extract import promote, scanner


def _mk_project_file(tmp_vault: Path, agent: str, stem: str, body: str = "project body") -> scanner.MemoryFile:
    mem_dir = tmp_vault / "bots" / agent / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    path = mem_dir / f"{stem}.md"
    content = f"---\nname: {stem}\ndescription: desc\ntype: project\n---\n{body}\n"
    path.write_text(content)
    return scanner._read_memory_file(path, agent=agent)


def test_promote_writes_direct_to_shared_project(tmp_vault: Path):
    state = scanner.ExtractionState(last_run=None, entries={})
    f = _mk_project_file(tmp_vault, "sg-imports", "project_china_portal_decisions")
    result = promote.promote_projects([f], state, tmp_vault)
    target = tmp_vault / "shared" / "project" / "sg-imports__china-portal-decisions.md"
    assert target.exists()
    assert result.written_fresh == ["project/sg-imports__china-portal-decisions"]


def test_promote_entry_has_direct_status(tmp_vault: Path):
    state = scanner.ExtractionState(last_run=None, entries={})
    f = _mk_project_file(tmp_vault, "clubinho", "project_shipment_rules")
    promote.promote_projects([f], state, tmp_vault)
    entry = state.entries["project/clubinho__shipment-rules"]
    assert entry.status == "direct"


def test_promote_skips_unchanged(tmp_vault: Path):
    state = scanner.ExtractionState(last_run=None, entries={})
    f = _mk_project_file(tmp_vault, "a", "project_x")
    promote.promote_projects([f], state, tmp_vault)
    # Second run without changes
    result = promote.promote_projects([f], state, tmp_vault)
    assert result.written_fresh == []
    assert "project/a__x" in result.unchanged_skipped


def test_promote_overwrite_when_source_changes(tmp_vault: Path):
    state = scanner.ExtractionState(last_run=None, entries={})
    f = _mk_project_file(tmp_vault, "a", "project_x", body="v1")
    promote.promote_projects([f], state, tmp_vault)
    # Mutate source
    f2 = _mk_project_file(tmp_vault, "a", "project_x", body="v2")
    result = promote.promote_projects([f2], state, tmp_vault)
    target = tmp_vault / "shared" / "project" / "a__x.md"
    assert "v2" in target.read_text()
    assert "project/a__x" in result.overwrite_safe


def test_promote_sibling_when_user_edits_promoted_file(tmp_vault: Path):
    state = scanner.ExtractionState(last_run=None, entries={})
    f = _mk_project_file(tmp_vault, "a", "project_x", body="v1")
    promote.promote_projects([f], state, tmp_vault)

    target = tmp_vault / "shared" / "project" / "a__x.md"
    target.write_text(target.read_text() + "\n\n(user note)\n")

    f2 = _mk_project_file(tmp_vault, "a", "project_x", body="v2")
    result = promote.promote_projects([f2], state, tmp_vault)

    sibling = tmp_vault / "shared" / "project" / "a__x.proposed.md"
    assert sibling.exists()
    assert "(user note)" in target.read_text()
    assert len(result.sibling_proposed) == 1


def test_promote_respects_user_deletion(tmp_vault: Path):
    state = scanner.ExtractionState(last_run=None, entries={})
    f = _mk_project_file(tmp_vault, "a", "project_x", body="v1")
    promote.promote_projects([f], state, tmp_vault)

    target = tmp_vault / "shared" / "project" / "a__x.md"
    target.unlink()

    f2 = _mk_project_file(tmp_vault, "a", "project_x", body="v2")
    result = promote.promote_projects([f2], state, tmp_vault)
    assert not target.exists()
    assert "project/a__x" in result.dismissed_skipped


def test_promote_namespaces_by_agent(tmp_vault: Path):
    state = scanner.ExtractionState(last_run=None, entries={})
    f1 = _mk_project_file(tmp_vault, "agent-a", "project_same_name", body="a body")
    f2 = _mk_project_file(tmp_vault, "agent-b", "project_same_name", body="b body")
    promote.promote_projects([f1, f2], state, tmp_vault)
    target_a = tmp_vault / "shared" / "project" / "agent-a__same-name.md"
    target_b = tmp_vault / "shared" / "project" / "agent-b__same-name.md"
    assert target_a.exists() and target_b.exists()
    assert "a body" in target_a.read_text()
    assert "b body" in target_b.read_text()
