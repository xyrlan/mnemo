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


# --- v0.3.1: briefing files as additional extraction input -------------------


def test_scan_discovers_briefings_sessions_dir(tmp_vault: Path):
    """Files in bots/<agent>/briefings/sessions/*.md must be picked up by the scanner."""
    briefings_dir = tmp_vault / "bots" / "agent_a" / "briefings" / "sessions"
    briefings_dir.mkdir(parents=True)
    (briefings_dir / "sid42.md").write_text(
        "---\n"
        "type: briefing\n"
        "agent: agent_a\n"
        "session_id: sid42\n"
        "date: 2026-04-14\n"
        "duration_minutes: 42\n"
        "---\n\n"
        "# Briefing — agent_a — sid42\n\n"
        "## Decisions made\n"
        "- Chose Zustand over Redux because smaller API surface.\n"
    )

    result = scanner.scan(tmp_vault, _empty_state())

    # v0.3.1: briefings route through the feedback extraction path so their
    # durable content (Decisions made, Dead ends) gets mined into Tier 2 pages.
    feedback = result.by_type["feedback"]
    assert any("briefings/sessions/sid42.md" in str(f.path) for f in feedback), (
        f"briefing file must appear in feedback scan bucket; got {[str(f.path) for f in feedback]}"
    )
    hit = next(f for f in feedback if "sid42" in str(f.path))
    assert hit.agent == "agent_a"
    assert hit.type == "feedback"  # routed into feedback cluster regardless of frontmatter


def test_scan_briefing_and_memory_files_coexist(tmp_vault: Path):
    """Existing memory/*.md files and briefings/sessions/*.md both get scanned."""
    memory_dir = tmp_vault / "bots" / "agent_a" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "feedback_use_yarn.md").write_text(
        "---\nname: Use yarn\ntype: feedback\n---\nbody"
    )
    briefings_dir = tmp_vault / "bots" / "agent_a" / "briefings" / "sessions"
    briefings_dir.mkdir(parents=True)
    (briefings_dir / "sid.md").write_text(
        "---\ntype: briefing\nagent: agent_a\nsession_id: sid\n---\n# body"
    )

    result = scanner.scan(tmp_vault, _empty_state())
    feedback_paths = [str(f.path) for f in result.by_type["feedback"]]

    assert any("memory/feedback_use_yarn.md" in p for p in feedback_paths)
    assert any("briefings/sessions/sid.md" in p for p in feedback_paths)


def test_scan_briefing_unchanged_source_hash_skipped_on_second_run(tmp_vault: Path):
    briefings_dir = tmp_vault / "bots" / "agent_a" / "briefings" / "sessions"
    briefings_dir.mkdir(parents=True)
    (briefings_dir / "sid.md").write_text(
        "---\ntype: briefing\nagent: agent_a\nsession_id: sid\n---\n# body"
    )

    first = scanner.scan(tmp_vault, _empty_state())
    # Seed state with the briefing's hash
    state = _empty_state()
    brief = next(f for f in first.by_type["feedback"] if "sid" in str(f.path))
    key = f"feedback/{brief.slug}"
    state.entries[key] = scanner.StateEntry(
        source_files=[str(brief.path)],
        source_hash=brief.source_hash,
        written_hash="",
        written_at="",
        status="inbox",
    )

    second = scanner.scan(tmp_vault, state)
    # Briefing is still listed, but marked unchanged (not dirty)
    assert key in second.unchanged_slugs
    assert not any(f.source_hash == brief.source_hash for f in second.dirty_files)


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


def test_state_entry_has_last_sync_field_defaulting_empty():
    from mnemo.core.extract.scanner import StateEntry

    entry = StateEntry(
        source_files=["bots/a/memory/x.md"],
        source_hash="sha256:abc",
        written_hash="sha256:def",
        written_at="2026-04-13T12:00:00",
        status="inbox",
    )
    assert entry.last_sync == ""


def test_extraction_state_default_schema_version_is_2():
    from mnemo.core.extract.scanner import ExtractionState

    state = ExtractionState(last_run=None)
    assert state.schema_version == 2
