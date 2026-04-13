"""Unit tests for core/extract/inbox.py — the decision table + writes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mnemo.core.extract import inbox, scanner


def _hash(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def _page(slug: str, type_: str = "feedback", body: str = "body", sources: list[str] | None = None):
    sources = sources or [f"bots/a/memory/{slug}.md"]
    src_hash = _hash("".join(sources) + body)
    return inbox.ExtractedPage(
        slug=slug,
        type=type_,
        name=slug.replace("-", " ").title(),
        description=f"desc for {slug}",
        body=body,
        source_files=sources,
        source_hash=src_hash,
    )


def _empty_state() -> scanner.ExtractionState:
    return scanner.ExtractionState(last_run=None, entries={})


# --- Task 7: fresh / unchanged ---------------------------------------------


def test_fresh_write_creates_inbox_file(tmp_vault: Path):
    state = _empty_state()
    page = _page("use-yarn")
    result = inbox.apply_pages([page], state, tmp_vault)
    target = tmp_vault / "shared" / "_inbox" / "feedback" / "use-yarn.md"
    assert target.exists()
    assert "use yarn" in target.read_text().lower()
    assert state.entries["feedback/use-yarn"].status == "inbox"
    assert result.written_fresh == ["feedback/use-yarn"]
    assert result.overwrite_safe == []


def test_frontmatter_contains_required_fields(tmp_vault: Path):
    state = _empty_state()
    page = _page("use-yarn", sources=["bots/a/memory/feedback_use_yarn.md"])
    inbox.apply_pages([page], state, tmp_vault)
    text = (tmp_vault / "shared" / "_inbox" / "feedback" / "use-yarn.md").read_text()
    assert "name:" in text
    assert "description:" in text
    assert "type: feedback" in text
    assert "extracted_at:" in text
    assert "sources:" in text
    assert "bots/a/memory/feedback_use_yarn.md" in text
    assert "needs-review" in text  # tag


def test_unchanged_source_hash_skips_silently(tmp_vault: Path):
    state = _empty_state()
    page = _page("use-yarn")
    inbox.apply_pages([page], state, tmp_vault)

    # Second run with same page
    result2 = inbox.apply_pages([page], state, tmp_vault)
    assert result2.written_fresh == []
    assert result2.overwrite_safe == []
    assert "feedback/use-yarn" in result2.unchanged_skipped


# --- Task 8: overwrite / sibling / promoted / dismissed --------------------


def test_overwrite_safe_when_source_changes_and_disk_matches(tmp_vault: Path):
    state = _empty_state()
    page_v1 = _page("use-yarn", body="v1")
    inbox.apply_pages([page_v1], state, tmp_vault)

    # Source changes; file on disk still matches what we wrote
    page_v2 = inbox.ExtractedPage(
        slug="use-yarn",
        type="feedback",
        name="Use yarn",
        description="desc",
        body="v2",
        source_files=["bots/a/memory/use-yarn.md"],
        source_hash=_hash("different source hash"),
    )
    result = inbox.apply_pages([page_v2], state, tmp_vault)
    target = tmp_vault / "shared" / "_inbox" / "feedback" / "use-yarn.md"
    assert "v2" in target.read_text()
    assert result.overwrite_safe == ["feedback/use-yarn"]


def test_sibling_proposed_when_user_edited_inbox(tmp_vault: Path):
    state = _empty_state()
    page_v1 = _page("use-yarn", body="original body")
    inbox.apply_pages([page_v1], state, tmp_vault)

    # User edits the inbox file by hand
    target = tmp_vault / "shared" / "_inbox" / "feedback" / "use-yarn.md"
    target.write_text(target.read_text() + "\n\n(user's handwritten note)\n")

    # Source hash changes triggering reapply
    page_v2 = inbox.ExtractedPage(
        slug="use-yarn",
        type="feedback",
        name="Use yarn",
        description="desc",
        body="new upstream body",
        source_files=["bots/a/memory/feedback_use_yarn.md"],
        source_hash=_hash("mutated source"),
    )
    result = inbox.apply_pages([page_v2], state, tmp_vault)

    sibling = tmp_vault / "shared" / "_inbox" / "feedback" / "use-yarn.proposed.md"
    assert sibling.exists()
    assert "new upstream body" in sibling.read_text()
    assert "(user's handwritten note)" in target.read_text()  # untouched
    assert len(result.sibling_proposed) == 1
    assert result.sibling_proposed[0][0] == "feedback/use-yarn"


def test_promoted_slug_writes_update_proposed(tmp_vault: Path):
    state = _empty_state()
    page_v1 = _page("use-yarn", body="v1")
    inbox.apply_pages([page_v1], state, tmp_vault)

    # Simulate user promoting: remove from _inbox, create under shared/feedback/
    target = tmp_vault / "shared" / "_inbox" / "feedback" / "use-yarn.md"
    promoted = tmp_vault / "shared" / "feedback" / "use-yarn.md"
    promoted.parent.mkdir(parents=True)
    promoted.write_text(target.read_text())
    target.unlink()

    # Source hash changes
    page_v2 = inbox.ExtractedPage(
        slug="use-yarn",
        type="feedback",
        name="Use yarn",
        description="desc",
        body="v2 upstream",
        source_files=["bots/a/memory/feedback_use_yarn.md"],
        source_hash=_hash("new source"),
    )
    result = inbox.apply_pages([page_v2], state, tmp_vault)

    update = tmp_vault / "shared" / "_inbox" / "feedback" / "use-yarn.update-proposed.md"
    assert update.exists()
    assert "v2 upstream" in update.read_text()
    assert len(result.update_proposed) == 1
    assert state.entries["feedback/use-yarn"].status == "promoted"


def test_dismissed_slug_is_not_resurrected(tmp_vault: Path):
    state = _empty_state()
    page_v1 = _page("use-yarn", body="v1")
    inbox.apply_pages([page_v1], state, tmp_vault)

    # User deletes from _inbox without promoting
    target = tmp_vault / "shared" / "_inbox" / "feedback" / "use-yarn.md"
    target.unlink()

    # Source changes
    page_v2 = inbox.ExtractedPage(
        slug="use-yarn",
        type="feedback",
        name="Use yarn",
        description="desc",
        body="v2",
        source_files=["bots/a/memory/feedback_use_yarn.md"],
        source_hash=_hash("new"),
    )
    result = inbox.apply_pages([page_v2], state, tmp_vault)
    assert not target.exists()  # NOT resurrected
    assert "feedback/use-yarn" in result.dismissed_skipped
    assert state.entries["feedback/use-yarn"].status == "dismissed"


def test_force_resurrects_dismissed(tmp_vault: Path):
    state = _empty_state()
    page_v1 = _page("use-yarn")
    inbox.apply_pages([page_v1], state, tmp_vault)

    target = tmp_vault / "shared" / "_inbox" / "feedback" / "use-yarn.md"
    target.unlink()

    page_v2 = _page("use-yarn", body="new")
    result = inbox.apply_pages([page_v2], state, tmp_vault, force=True)
    assert target.exists()
    assert "new" in target.read_text()


# --- Task 9: dedupe / atomic state -----------------------------------------


def test_dedupe_by_slug_merges_source_files(tmp_vault: Path):
    page_a = inbox.ExtractedPage(
        slug="use-yarn", type="feedback",
        name="Use yarn", description="d",
        body="short",
        source_files=["bots/x/memory/feedback_use_yarn.md"],
        source_hash=_hash("x"),
    )
    page_b = inbox.ExtractedPage(
        slug="use-yarn", type="feedback",
        name="Use yarn (v2)", description="d",
        body="this body is longer because it has more sources backing it",
        source_files=[
            "bots/y/memory/feedback_use_yarn.md",
            "bots/z/memory/feedback_yarn_only.md",
        ],
        source_hash=_hash("y"),
    )
    merged = inbox.dedupe_by_slug([page_a, page_b])
    assert len(merged) == 1
    assert set(merged[0].source_files) == {
        "bots/x/memory/feedback_use_yarn.md",
        "bots/y/memory/feedback_use_yarn.md",
        "bots/z/memory/feedback_yarn_only.md",
    }
    # Body from the larger cluster (page_b has more source files)
    assert "longer" in merged[0].body


def test_dedupe_preserves_unique_slugs():
    a = _page("slug-a")
    b = _page("slug-b")
    merged = inbox.dedupe_by_slug([a, b])
    assert len(merged) == 2


def test_atomic_write_state_roundtrip(tmp_vault: Path):
    state = scanner.ExtractionState(last_run="2026-04-13T10:00:00", entries={
        "feedback/x": scanner.StateEntry(
            source_files=["bots/a/memory/x.md"],
            source_hash="sha256:aaa",
            written_hash="sha256:bbb",
            written_at="2026-04-13T10:00:00",
            status="inbox",
        )
    })
    path = tmp_vault / ".mnemo" / "extraction-state.json"
    inbox.atomic_write_state(state, path)
    assert path.exists()

    loaded = inbox.load_state(path)
    assert loaded.last_run == "2026-04-13T10:00:00"
    assert loaded.entries["feedback/x"].source_hash == "sha256:aaa"
    assert loaded.entries["feedback/x"].status == "inbox"


def test_load_state_missing_returns_empty(tmp_vault: Path):
    state = inbox.load_state(tmp_vault / ".mnemo" / "nope.json")
    assert state.last_run is None
    assert state.entries == {}


def test_load_state_corrupt_backs_up_and_returns_empty(tmp_vault: Path):
    path = tmp_vault / ".mnemo" / "extraction-state.json"
    path.parent.mkdir(parents=True)
    path.write_text("this is not json")
    state = inbox.load_state(path)
    assert state.entries == {}
    # Backup sibling exists
    backups = list(path.parent.glob("extraction-state.json.bak.*"))
    assert len(backups) == 1


def test_load_state_unknown_schema_version_raises(tmp_vault: Path):
    path = tmp_vault / ".mnemo" / "extraction-state.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 999, "last_run": None, "entries": {}}))
    with pytest.raises(inbox.StateSchemaError):
        inbox.load_state(path)
