"""Universal-promotion regression: cross-project source_files union.

Background: in v0.15 dogfood the activation index showed 0 universal
rules even though many slugs had been mined from multiple projects'
briefings. Root cause: ``_apply_auto_promoted`` overwrote
``state.entries[key].source_files`` with the freshly-extracted page's
``source_files`` instead of unioning with the prior list, so the
``projects_for_rule`` resolver never observed >=2 distinct ``bots/<name>/``
prefixes.

This test pins the union behaviour on the auto-promoted branch.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.extract.inbox.branches.auto_promoted import _apply_auto_promoted
from mnemo.core.extract.inbox.types import ApplyResult, ExtractedPage
from mnemo.core.extract.scanner import ExtractionState, StateEntry


def _page(*sources: str) -> ExtractedPage:
    return ExtractedPage(
        slug="x",
        source_hash=f"sha256:{','.join(sources)}",
        name="X",
        description="X",
        type="feedback",
        body="Body long enough to clear render gates.",
        source_files=list(sources),
        tags=["git"],
        stability="stable",
        enforce=None,
        activates_on=None,
    )


def test_cross_project_sources_union_on_reextraction(tmp_path: Path) -> None:
    vault = tmp_path
    target_dir = vault / "shared" / "feedback"
    target_dir.mkdir(parents=True)
    target = target_dir / "x.md"

    state = ExtractionState(last_run=None)
    key = "feedback/x"

    # First extraction: project A only.
    page_a = _page("bots/proj-a/briefings/sessions/aaa.md")
    _apply_auto_promoted(
        page_a, None, target, vault, state,
        run_id="2026-01-01T00:00:00", force=False, result=ApplyResult(),
    )
    assert key in state.entries
    assert state.entries[key].source_files == [
        "bots/proj-a/briefings/sessions/aaa.md"
    ]

    # Second extraction: same slug from project B's briefing. The target
    # exists and is unedited (matches the prior written_hash), so the
    # _handle_target_exists branch runs — and the prior project-A source
    # must survive the rewrite.
    page_b = _page("bots/proj-b/briefings/sessions/bbb.md")
    _apply_auto_promoted(
        page_b, state.entries[key], target, vault, state,
        run_id="2026-01-02T00:00:00", force=False, result=ApplyResult(),
    )

    assert sorted(state.entries[key].source_files) == [
        "bots/proj-a/briefings/sessions/aaa.md",
        "bots/proj-b/briefings/sessions/bbb.md",
    ]

    rendered = target.read_text()
    assert "bots/proj-a/briefings/sessions/aaa.md" in rendered
    assert "bots/proj-b/briefings/sessions/bbb.md" in rendered


def test_first_extraction_does_not_duplicate_sources(tmp_path: Path) -> None:
    """When entry is None (first write), the union helper must be a no-op."""
    vault = tmp_path
    target_dir = vault / "shared" / "feedback"
    target_dir.mkdir(parents=True)
    target = target_dir / "x.md"

    state = ExtractionState(last_run=None)
    page = _page("bots/only/briefings/sessions/aaa.md")
    _apply_auto_promoted(
        page, None, target, vault, state,
        run_id="2026-01-01T00:00:00", force=False, result=ApplyResult(),
    )

    assert state.entries["feedback/x"].source_files == [
        "bots/only/briefings/sessions/aaa.md"
    ]
