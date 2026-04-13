"""Unit tests for core/extract/scanner.py."""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo.core.extract import scanner


def _empty_state() -> scanner.ExtractionState:
    return scanner.ExtractionState(last_run=None, entries={})


def test_scan_empty_vault_returns_empty(tmp_vault: Path):
    result = scanner.scan(tmp_vault, _empty_state())
    assert result.by_type == {"feedback": [], "user": [], "reference": [], "project": []}
    assert result.dirty_files == []
    assert result.unchanged_slugs == set()


def test_scan_groups_files_by_type(populated_vault: Path):
    result = scanner.scan(populated_vault, _empty_state())
    assert len(result.by_type["feedback"]) == 3  # use_yarn + no_commits + no_commit_without_permission
    assert len(result.by_type["project"]) == 1   # china_portal
    assert result.by_type["user"] == []
    assert result.by_type["reference"] == []


def test_scan_skips_memory_md_index(populated_vault: Path):
    result = scanner.scan(populated_vault, _empty_state())
    all_files = [f for lst in result.by_type.values() for f in lst]
    assert all("MEMORY.md" not in str(f.path) for f in all_files)


def test_scan_agent_name_derived_from_directory(populated_vault: Path):
    result = scanner.scan(populated_vault, _empty_state())
    agents = {f.agent for lst in result.by_type.values() for f in lst}
    assert agents == {"agent-a", "agent-b"}


def test_scan_source_hash_is_deterministic(populated_vault: Path):
    first = scanner.scan(populated_vault, _empty_state())
    second = scanner.scan(populated_vault, _empty_state())
    h1 = sorted(f.source_hash for lst in first.by_type.values() for f in lst)
    h2 = sorted(f.source_hash for lst in second.by_type.values() for f in lst)
    assert h1 == h2


def test_scan_unknown_type_defaults_to_feedback(tmp_vault: Path):
    agent_dir = tmp_vault / "bots" / "x" / "memory"
    agent_dir.mkdir(parents=True)
    (agent_dir / "weird.md").write_text(
        "---\nname: Weird\ndescription: Weird\ntype: alien\n---\nbody"
    )
    result = scanner.scan(tmp_vault, _empty_state())
    assert len(result.by_type["feedback"]) == 1
    assert result.by_type["feedback"][0].type == "feedback"


def test_scan_missing_frontmatter_defaults_to_feedback(tmp_vault: Path):
    agent_dir = tmp_vault / "bots" / "x" / "memory"
    agent_dir.mkdir(parents=True)
    (agent_dir / "bare.md").write_text("no frontmatter at all\njust body")
    result = scanner.scan(tmp_vault, _empty_state())
    assert len(result.by_type["feedback"]) == 1


def test_scan_diff_against_state_marks_unchanged(populated_vault: Path):
    first = scanner.scan(populated_vault, _empty_state())
    # Build a state where every discovered slug is "known"
    entries = {}
    for lst in first.by_type.values():
        for f in lst:
            entries[f"{f.type}/{f.slug}"] = scanner.StateEntry(
                source_files=[str(f.path)],
                source_hash=f.source_hash,
                written_hash="irrelevant",
                written_at="2026-04-13T00:00:00",
                status="inbox",
            )
    state = scanner.ExtractionState(last_run="2026-04-13T00:00:00", entries=entries)
    second = scanner.scan(populated_vault, state)
    # All files should be marked as unchanged (nothing dirty)
    assert second.dirty_files == []
    assert len(second.unchanged_slugs) > 0


def test_scan_detects_content_change(populated_vault: Path):
    first = scanner.scan(populated_vault, _empty_state())
    # Mutate one file
    for f in first.by_type["feedback"]:
        if "use_yarn" in f.path.name:
            f.path.write_text(f.path.read_text() + "\n\n(updated)\n")
            break
    result = scanner.scan(populated_vault, _empty_state())
    yarn_file = next(f for f in result.by_type["feedback"] if "use_yarn" in f.path.name)
    # Different content => different hash
    old_hash = next(f.source_hash for f in first.by_type["feedback"] if "use_yarn" in f.path.name)
    assert yarn_file.source_hash != old_hash
