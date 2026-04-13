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
