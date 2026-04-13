"""v0.3 delta tests for inbox target selection and decision rows."""
from __future__ import annotations

from pathlib import Path

from mnemo.core.extract import inbox
from mnemo.core.extract.inbox import ExtractedPage


def _page(slug, *, sources, type="feedback"):
    return ExtractedPage(
        slug=slug,
        type=type,
        name=slug.replace("-", " ").title(),
        description=f"{slug} description",
        body=f"Body for {slug}.\n",
        source_files=list(sources),
        source_hash="sha256:" + slug,
    )


def test_target_single_source_goes_to_sacred(tmp_path):
    page = _page("use-yarn", sources=["bots/a/memory/feedback_use_yarn.md"])
    target = inbox._target_path_for_page(page, tmp_path)
    assert target == tmp_path / "shared" / "feedback" / "use-yarn.md"


def test_target_multi_source_goes_to_inbox(tmp_path):
    page = _page("no-commits", sources=[
        "bots/a/memory/feedback_no_commits.md",
        "bots/b/memory/feedback_no_commit_without_permission.md",
    ])
    target = inbox._target_path_for_page(page, tmp_path)
    assert target == tmp_path / "shared" / "_inbox" / "feedback" / "no-commits.md"


def test_target_three_plus_sources_goes_to_inbox(tmp_path):
    page = _page("many", sources=["a", "b", "c", "d"])
    target = inbox._target_path_for_page(page, tmp_path)
    assert target == tmp_path / "shared" / "_inbox" / "feedback" / "many.md"


def test_is_auto_promoted_target_true_for_shared(tmp_path):
    target = tmp_path / "shared" / "feedback" / "use-yarn.md"
    assert inbox._is_auto_promoted_target(target, tmp_path) is True


def test_is_auto_promoted_target_false_for_inbox(tmp_path):
    target = tmp_path / "shared" / "_inbox" / "feedback" / "use-yarn.md"
    assert inbox._is_auto_promoted_target(target, tmp_path) is False


def test_sibling_path_bounces_auto_promoted_to_inbox(tmp_path):
    target = tmp_path / "shared" / "feedback" / "use-yarn.md"
    sibling = inbox._sibling_path(target, tmp_path)
    assert sibling == tmp_path / "shared" / "_inbox" / "feedback" / "use-yarn.proposed.md"


def test_sibling_path_stays_adjacent_for_inbox_target(tmp_path):
    target = tmp_path / "shared" / "_inbox" / "feedback" / "no-commits.md"
    sibling = inbox._sibling_path(target, tmp_path)
    assert sibling == tmp_path / "shared" / "_inbox" / "feedback" / "no-commits.proposed.md"


def test_render_page_inbox_has_needs_review_tag():
    page = _page("use-yarn", sources=["bots/a/memory/feedback_use_yarn.md"])
    content = inbox._render_page(page, run_id="2026-04-13T12:00:00-abc123", auto_promoted=False)
    assert "needs-review" in content
    assert "auto-promoted" not in content
    assert "last_sync:" not in content


def test_render_page_auto_promoted_has_auto_tag_and_last_sync():
    page = _page("use-yarn", sources=["bots/a/memory/feedback_use_yarn.md"])
    content = inbox._render_page(page, run_id="2026-04-13T12:00:00-abc123", auto_promoted=True)
    assert "auto-promoted" in content
    assert "needs-review" not in content
    assert "last_sync: 2026-04-13T12:00:00-abc123" in content
    assert "extracted_at: 2026-04-13T12:00:00-abc123" in content


def test_apply_result_has_v0_3_fields():
    result = inbox.ApplyResult()
    assert result.auto_promoted == []
    assert result.sibling_bounced == []
    assert result.upgrade_proposed == []


from mnemo.core.extract.scanner import ExtractionState, StateEntry


def _mkstate(**entries):
    state = ExtractionState(last_run=None)
    for key, entry in entries.items():
        state.entries[key] = entry
    return state


def test_apply_single_source_fresh_writes_sacred_with_auto_promoted_status(tmp_path):
    page = _page("use-yarn", sources=["bots/a/memory/feedback_use_yarn.md"])
    state = _mkstate()

    result = inbox.apply_pages([page], state, tmp_path, run_id="2026-04-13T12:00:00-run1")

    target = tmp_path / "shared" / "feedback" / "use-yarn.md"
    assert target.exists(), "sacred file should be written"
    content = target.read_text()
    assert "auto-promoted" in content
    assert "last_sync: 2026-04-13T12:00:00-run1" in content

    entry = state.entries["feedback/use-yarn"]
    assert entry.status == "auto_promoted"
    assert entry.last_sync == "2026-04-13T12:00:00-run1"
    assert result.auto_promoted == ["feedback/use-yarn"]
    assert result.written_fresh == []


def test_apply_single_source_overwrite_safe_updates_sacred(tmp_path):
    page_v1 = _page("use-yarn", sources=["bots/a/memory/feedback_use_yarn.md"])

    state = _mkstate()
    inbox.apply_pages([page_v1], state, tmp_path, run_id="2026-04-13T12:00:00-run1")

    page_v2 = _page("use-yarn", sources=["bots/a/memory/feedback_use_yarn.md"])
    page_v2.source_hash = "sha256:newhash"
    page_v2.body = "Updated body for use-yarn.\n"

    result = inbox.apply_pages([page_v2], state, tmp_path, run_id="2026-04-13T13:00:00-run2")

    target = tmp_path / "shared" / "feedback" / "use-yarn.md"
    assert "Updated body" in target.read_text()
    assert result.overwrite_safe == ["feedback/use-yarn"]
    entry = state.entries["feedback/use-yarn"]
    assert entry.status == "auto_promoted"
    assert entry.source_hash == "sha256:newhash"
    assert entry.last_sync == "2026-04-13T13:00:00-run2"


def test_apply_single_source_dismissed_when_user_deleted_sacred_file(tmp_path):
    page_v1 = _page("use-yarn", sources=["bots/a/memory/feedback_use_yarn.md"])
    state = _mkstate()
    inbox.apply_pages([page_v1], state, tmp_path, run_id="2026-04-13T12:00:00-run1")

    (tmp_path / "shared" / "feedback" / "use-yarn.md").unlink()

    page_v2 = _page("use-yarn", sources=["bots/a/memory/feedback_use_yarn.md"])
    page_v2.source_hash = "sha256:newhash"
    result = inbox.apply_pages([page_v2], state, tmp_path, run_id="2026-04-13T14:00:00-run3")

    target = tmp_path / "shared" / "feedback" / "use-yarn.md"
    assert not target.exists(), "dismissed files are not resurrected"
    entry = state.entries["feedback/use-yarn"]
    assert entry.status == "dismissed"
    assert result.dismissed_skipped == ["feedback/use-yarn"]
