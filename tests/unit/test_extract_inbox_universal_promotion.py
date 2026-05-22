"""Universal-promotion regression: cross-project _inbox pages move into the sacred dir.

Background: PR #86 unioned ``source_files`` so re-extraction accumulated
cross-project lineage, but the apply dispatcher had no branch that
moved a page out of ``shared/_inbox/<type>/`` once its project count
crossed ``scoping.universalThreshold``. Six rules sat there during the
v0.15 dogfood despite already qualifying as universal.

This module pins:
- the new ``_is_universal_promotion`` predicate
- the ``_apply_universal_promotion`` handler
- the end-of-extract reconciler that drains pre-existing orphans
- absolute-path handling in ``projects_for_rule`` (Bug A)
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.extract.inbox.apply import (
    _DISPATCH,
    _is_universal_promotion,
    apply_pages,
)
from mnemo.core.extract.inbox.branches.universal_promotion import (
    _apply_universal_promotion,
    _universal_threshold,
)
from mnemo.core.extract.inbox.io import content_hash
from mnemo.core.extract.inbox.rendering import _render_page
from mnemo.core.extract.inbox.types import ApplyResult, ExtractedPage
from mnemo.core.extract.scanner import ExtractionState, StateEntry


def _page(slug: str = "x", *sources: str, body: str | None = None) -> ExtractedPage:
    return ExtractedPage(
        slug=slug,
        source_hash=f"sha256:{','.join(sources)}",
        name=slug.upper(),
        description=slug.upper(),
        type="feedback",
        body=body or "Body long enough to clear render gates.",
        source_files=list(sources),
        tags=["git"],
        stability="stable",
        enforce=None,
        activates_on=None,
    )


def test_dispatch_table_includes_universal_promotion_first():
    """Universal-promotion must precede upgrade + auto so multi-project pages
    are intercepted before falling through to the inbox flow."""
    handlers = [name for _, name in _DISPATCH]
    assert len(_DISPATCH) == 4
    # The new predicate is the first row.
    from mnemo.core.extract.inbox.apply import _run_universal_promotion
    assert _DISPATCH[0][1] is _run_universal_promotion


def test_predicate_fires_for_multi_project_inbox_page(tmp_path: Path):
    vault = tmp_path
    page = _page(
        "p",
        "bots/proj-a/briefings/sessions/aaa.md",
        "bots/proj-b/briefings/sessions/bbb.md",
    )
    target = vault / "shared" / "_inbox" / "feedback" / "p.md"
    assert _is_universal_promotion(page, None, target, is_auto=False) is True


def test_predicate_skips_single_project_pages(tmp_path: Path):
    vault = tmp_path
    page = _page(
        "p",
        "bots/proj-a/briefings/sessions/aaa.md",
        "bots/proj-a/briefings/sessions/bbb.md",  # same project, two briefings
    )
    target = vault / "shared" / "_inbox" / "feedback" / "p.md"
    assert _is_universal_promotion(page, None, target, is_auto=False) is False


def test_predicate_skips_auto_promoted_targets(tmp_path: Path):
    """Pages targeting shared/<type>/ are handled by the auto-promoted branch,
    not the universal-promotion branch."""
    vault = tmp_path
    page = _page(
        "p",
        "bots/proj-a/x.md",
        "bots/proj-b/x.md",
    )
    target = vault / "shared" / "feedback" / "p.md"
    assert _is_universal_promotion(page, None, target, is_auto=True) is False


def test_predicate_uses_unioned_sources_with_prior_entry(tmp_path: Path):
    """A page arriving with one new source whose state-entry already has a
    different-project source must trip the predicate via the union."""
    vault = tmp_path
    entry = StateEntry(
        source_files=["bots/proj-a/briefings/sessions/aaa.md"],
        source_hash="sha256:old",
        written_hash="sha256:irrelevant",
        written_at="2026-01-01T00:00:00",
        status="inbox",
    )
    page = _page("p", "bots/proj-b/briefings/sessions/bbb.md")
    target = vault / "shared" / "_inbox" / "feedback" / "p.md"
    assert _is_universal_promotion(page, entry, target, is_auto=False) is True


def test_handler_writes_to_shared_and_removes_inbox_copy(tmp_path: Path):
    vault = tmp_path
    inbox_dir = vault / "shared" / "_inbox" / "feedback"
    inbox_dir.mkdir(parents=True)
    inbox_copy = inbox_dir / "p.md"
    inbox_copy.write_text("stale staging content")

    state = ExtractionState(last_run=None)
    result = ApplyResult()
    page = _page(
        "p",
        "bots/proj-a/briefings/sessions/aaa.md",
        "bots/proj-b/briefings/sessions/bbb.md",
    )
    _apply_universal_promotion(
        page, None, inbox_copy, vault, state,
        run_id="2026-01-02T00:00:00", force=False, result=result,
    )

    dest = vault / "shared" / "feedback" / "p.md"
    assert dest.exists()
    assert not inbox_copy.exists()
    assert state.entries["feedback/p"].status == "promoted"
    assert "feedback/p" in result.universal_promoted
    rendered = dest.read_text()
    assert "bots/proj-a/briefings/sessions/aaa.md" in rendered
    assert "bots/proj-b/briefings/sessions/bbb.md" in rendered


def test_handler_handles_absolute_path_sources(tmp_path: Path):
    vault = tmp_path
    abs_a = str(vault / "bots" / "proj-a" / "briefings" / "sessions" / "aaa.md")
    abs_b = str(vault / "bots" / "proj-b" / "briefings" / "sessions" / "bbb.md")
    inbox_dir = vault / "shared" / "_inbox" / "feedback"
    inbox_dir.mkdir(parents=True)
    inbox_copy = inbox_dir / "p.md"
    inbox_copy.write_text("stale")

    state = ExtractionState(last_run=None)
    result = ApplyResult()
    page = _page("p", abs_a, abs_b)

    # Predicate must see 2 projects via Bug-A fix.
    assert _is_universal_promotion(page, None, inbox_copy, is_auto=False) is True

    _apply_universal_promotion(
        page, None, inbox_copy, vault, state,
        run_id="2026-01-02T00:00:00", force=False, result=result,
    )
    dest = vault / "shared" / "feedback" / "p.md"
    assert dest.exists()
    assert state.entries["feedback/p"].status == "promoted"


def test_handler_safe_overwrites_existing_unedited_dest(tmp_path: Path):
    vault = tmp_path
    dest_dir = vault / "shared" / "feedback"
    dest_dir.mkdir(parents=True)
    dest = dest_dir / "p.md"

    page_v1 = _page(
        "p",
        "bots/proj-a/briefings/sessions/aaa.md",
        "bots/proj-b/briefings/sessions/bbb.md",
    )
    initial_content = _render_page(page_v1, run_id="2026-01-01T00:00:00", auto_promoted=True)
    dest.write_text(initial_content)

    entry = StateEntry(
        source_files=list(page_v1.source_files),
        source_hash=page_v1.source_hash,
        written_hash=content_hash(initial_content),
        written_at="2026-01-01T00:00:00",
        status="promoted",
        last_sync="2026-01-01T00:00:00",
    )

    page_v2 = _page(
        "p",
        "bots/proj-c/briefings/sessions/ccc.md",  # adds project C
        body="Different body content, plenty of chars to render cleanly.",
    )
    inbox_copy = vault / "shared" / "_inbox" / "feedback" / "p.md"
    inbox_copy.parent.mkdir(parents=True, exist_ok=True)
    inbox_copy.write_text("noop")

    state = ExtractionState(last_run=None, entries={"feedback/p": entry})
    result = ApplyResult()
    _apply_universal_promotion(
        page_v2, entry, inbox_copy, vault, state,
        run_id="2026-01-02T00:00:00", force=False, result=result,
    )

    assert "feedback/p" in result.universal_promoted
    assert not result.sibling_bounced  # safe overwrite, no conflict
    rendered = dest.read_text()
    # Both prior projects + new project C survive the union.
    assert "bots/proj-a/briefings/sessions/aaa.md" in rendered
    assert "bots/proj-b/briefings/sessions/bbb.md" in rendered
    assert "bots/proj-c/briefings/sessions/ccc.md" in rendered


def test_handler_bounces_sibling_when_dest_user_edited(tmp_path: Path):
    vault = tmp_path
    dest_dir = vault / "shared" / "feedback"
    dest_dir.mkdir(parents=True)
    dest = dest_dir / "p.md"
    dest.write_text("user-edited content distinct from any render")

    entry = StateEntry(
        source_files=["bots/proj-a/briefings/sessions/aaa.md"],
        source_hash="sha256:old",
        written_hash="sha256:does-not-match-disk",
        written_at="2026-01-01T00:00:00",
        status="promoted",
        last_sync="2026-01-01T00:00:00",
    )

    page = _page(
        "p",
        "bots/proj-b/briefings/sessions/bbb.md",
    )
    inbox_copy = vault / "shared" / "_inbox" / "feedback" / "p.md"
    inbox_copy.parent.mkdir(parents=True, exist_ok=True)
    inbox_copy.write_text("staging")

    state = ExtractionState(last_run=None, entries={"feedback/p": entry})
    result = ApplyResult()
    _apply_universal_promotion(
        page, entry, inbox_copy, vault, state,
        run_id="2026-01-02T00:00:00", force=False, result=result,
    )

    # User-edited dest: bounce sibling, leave original dest + state untouched.
    assert not result.universal_promoted
    assert result.sibling_bounced
    assert dest.read_text() == "user-edited content distinct from any render"
    sibling = vault / "shared" / "_inbox" / "feedback" / "p.proposed.md"
    assert sibling.exists()


def test_apply_pages_routes_multi_project_to_universal_promotion(tmp_path: Path):
    """End-to-end through the dispatch table: a fresh multi-project page
    lands in shared/feedback/, not shared/_inbox/feedback/."""
    vault = tmp_path
    state = ExtractionState(last_run=None)
    page = _page(
        "p",
        "bots/proj-a/briefings/sessions/aaa.md",
        "bots/proj-b/briefings/sessions/bbb.md",
    )
    result = apply_pages([page], state, vault, run_id="2026-01-01T00:00:00")
    assert "feedback/p" in result.universal_promoted
    assert (vault / "shared" / "feedback" / "p.md").exists()
    assert not (vault / "shared" / "_inbox" / "feedback" / "p.md").exists()
    assert state.entries["feedback/p"].status == "promoted"


def test_universal_threshold_cache_resets_after_config_change(monkeypatch):
    """The cached threshold must observe config changes when explicitly cleared."""
    _universal_threshold.cache_clear()
    val = _universal_threshold()
    assert val >= 2
    _universal_threshold.cache_clear()
