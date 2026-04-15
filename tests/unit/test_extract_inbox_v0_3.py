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


def test_apply_sibling_bounced_when_user_edited_sacred_file(tmp_path):
    page_v1 = _page("use-yarn", sources=["bots/a/memory/feedback_use_yarn.md"])
    state = _mkstate()
    inbox.apply_pages([page_v1], state, tmp_path, run_id="2026-04-13T12:00:00-run1")

    # User edits the sacred file
    target = tmp_path / "shared" / "feedback" / "use-yarn.md"
    target.write_text(target.read_text() + "\n\nUser's own paragraph.\n")

    page_v2 = _page("use-yarn", sources=["bots/a/memory/feedback_use_yarn.md"])
    page_v2.source_hash = "sha256:newhash"
    page_v2.body = "Updated body from LLM.\n"

    result = inbox.apply_pages([page_v2], state, tmp_path, run_id="2026-04-13T13:00:00-run2")

    sibling = tmp_path / "shared" / "_inbox" / "feedback" / "use-yarn.proposed.md"
    assert sibling.exists(), "sibling should be bounced into _inbox/"
    assert "Updated body from LLM" in sibling.read_text()
    assert "User's own paragraph" in target.read_text(), "sacred file must be untouched"
    assert result.sibling_bounced and result.sibling_bounced[0][0] == "feedback/use-yarn"
    entry = state.entries["feedback/use-yarn"]
    assert entry.status == "auto_promoted", "status must not regress"
    assert entry.last_sync == "2026-04-13T12:00:00-run1", "last_sync must not advance on bounce"


def test_apply_upgrade_proposed_when_single_becomes_multi(tmp_path):
    # Run 1: single-source, auto-promoted
    page_v1 = _page("use-yarn", sources=["bots/a/memory/feedback_use_yarn.md"])
    state = _mkstate()
    inbox.apply_pages([page_v1], state, tmp_path, run_id="2026-04-13T12:00:00-run1")

    target = tmp_path / "shared" / "feedback" / "use-yarn.md"
    assert target.exists()
    sacred_snapshot = target.read_text()

    # Run 2: the same slug now has 2 sources (a new agent memorized a yarn rule)
    page_v2 = _page("use-yarn", sources=[
        "bots/a/memory/feedback_use_yarn.md",
        "bots/b/memory/feedback_yarn_rule.md",
    ])
    page_v2.source_hash = "sha256:clusterhash"

    result = inbox.apply_pages([page_v2], state, tmp_path, run_id="2026-04-13T13:00:00-run2")

    sibling = tmp_path / "shared" / "_inbox" / "feedback" / "use-yarn.proposed.md"
    assert sibling.exists()
    assert "bots/b/memory/feedback_yarn_rule.md" in sibling.read_text()
    assert target.read_text() == sacred_snapshot, "sacred file untouched"
    entry = state.entries["feedback/use-yarn"]
    assert entry.status == "auto_promoted", "no regression to inbox status"
    assert len(entry.source_files) == 1, "existing entry preserved"
    assert result.upgrade_proposed and result.upgrade_proposed[0][0] == "feedback/use-yarn"


# --- v0.3.1: stability frontmatter field -------------------------------------


def _page_with_stability(slug, stability, sources):
    p = _page(slug, sources=sources)
    p.stability = stability
    return p


def test_render_page_emits_stability_stable_by_default():
    """Legacy ExtractedPage without stability attr defaults to 'stable' in frontmatter."""
    page = _page("use-yarn", sources=["bots/a/memory/feedback_use_yarn.md"])
    # Don't set stability explicitly — exercise the default branch.
    content = inbox._render_page(page, run_id="2026-04-14T12:00:00-run1", auto_promoted=True)
    assert "stability: stable" in content


def test_render_page_emits_stability_evolving_when_set():
    page = _page_with_stability(
        "still-deciding-zustand",
        stability="evolving",
        sources=["bots/a/memory/feedback_zustand.md"],
    )
    content = inbox._render_page(page, run_id="2026-04-14T12:00:00-run1", auto_promoted=True)
    assert "stability: evolving" in content
    assert "stability: stable" not in content


def test_render_page_inbox_branch_also_emits_stability():
    page = _page_with_stability(
        "two-source-rule",
        stability="stable",
        sources=[
            "bots/a/memory/feedback_x.md",
            "bots/b/memory/feedback_x.md",
        ],
    )
    content = inbox._render_page(page, run_id="2026-04-14T12:00:00-run1", auto_promoted=False)
    assert "stability: stable" in content
    assert "needs-review" in content  # existing tag, not regressed


def test_extracted_page_accepts_stability_field():
    """ExtractedPage dataclass exposes a stability field with default 'stable'."""
    page = inbox.ExtractedPage(
        slug="x",
        type="feedback",
        name="X",
        description="d",
        body="b",
        source_files=["bots/a/memory/x.md"],
        source_hash="sha256:x",
    )
    assert page.stability == "stable"


# --- v0.4: tags frontmatter field --------------------------------------------


def _page_with_tags(slug, tags, sources, stability="stable"):
    p = _page(slug, sources=sources)
    p.tags = list(tags)
    p.stability = stability
    return p


def test_render_page_auto_promoted_emits_system_marker_plus_topic_tags():
    page = _page_with_tags(
        "use-yarn",
        tags=["package-management", "workflow"],
        sources=["bots/a/memory/feedback_use_yarn.md"],
    )
    content = inbox._render_page(page, run_id="2026-04-14T12:00:00-r1", auto_promoted=True)
    assert "  - auto-promoted" in content
    assert "  - package-management" in content
    assert "  - workflow" in content
    # system marker should appear first so managed tags are at the head of the list
    tags_block = content.split("tags:\n", 1)[1].split("---", 1)[0]
    lines = [l.strip()[2:] for l in tags_block.splitlines() if l.strip().startswith("- ")]
    assert lines[0] == "auto-promoted"


def test_render_page_inbox_emits_needs_review_plus_topic_tags():
    page = _page_with_tags(
        "two-source-rule",
        tags=["auth", "oauth2"],
        sources=["bots/a/memory/x.md", "bots/b/memory/x.md"],
    )
    content = inbox._render_page(page, run_id="2026-04-14T12:00:00-r1", auto_promoted=False)
    assert "  - needs-review" in content
    assert "  - auth" in content
    assert "  - oauth2" in content
    assert "  - auto-promoted" not in content


def test_render_page_drops_duplicate_system_marker_from_topic_tags():
    """If the LLM (or a merge) sneaks 'auto-promoted' into page.tags, don't emit it twice."""
    page = _page_with_tags(
        "x",
        tags=["auto-promoted", "git"],
        sources=["bots/a/memory/x.md"],
    )
    content = inbox._render_page(page, run_id="r1", auto_promoted=True)
    assert content.count("  - auto-promoted") == 1
    assert "  - git" in content


def test_render_page_with_empty_tags_still_has_system_marker():
    page = _page("x", sources=["bots/a/memory/x.md"])
    content = inbox._render_page(page, run_id="r1", auto_promoted=True)
    assert "  - auto-promoted" in content


def test_extracted_page_tags_defaults_to_empty_list():
    page = inbox.ExtractedPage(
        slug="x", type="feedback", name="X", description="d", body="b",
        source_files=["bots/a/memory/x.md"], source_hash="sha256:x",
    )
    assert page.tags == []


def test_dedupe_unions_tags_across_merged_pages():
    page_a = inbox.ExtractedPage(
        slug="use-yarn", type="feedback",
        name="Use yarn", description="d", body="body-a",
        source_files=["bots/x/memory/feedback_use_yarn.md"],
        source_hash="sha256:a",
        tags=["package-management"],
    )
    page_b = inbox.ExtractedPage(
        slug="use-yarn", type="feedback",
        name="Use yarn", description="d", body="longer body for larger cluster",
        source_files=[
            "bots/y/memory/feedback_use_yarn.md",
            "bots/z/memory/feedback_yarn_only.md",
        ],
        source_hash="sha256:b",
        tags=["workflow", "package-management"],
    )
    merged = inbox.dedupe_by_slug([page_a, page_b])
    assert len(merged) == 1
    assert set(merged[0].tags) == {"package-management", "workflow"}


def test_dedupe_preserves_stability_from_chosen_cluster():
    """Pre-v0.4 dedupe dropped stability on the floor — regression guard."""
    page_a = inbox.ExtractedPage(
        slug="x", type="feedback", name="X", description="d", body="a",
        source_files=["bots/a/memory/x.md"], source_hash="sha256:a",
        stability="evolving",
    )
    page_b = inbox.ExtractedPage(
        slug="x", type="feedback", name="X", description="d", body="b",
        source_files=["bots/b/memory/x.md", "bots/c/memory/x.md"],
        source_hash="sha256:b",
        stability="stable",
    )
    merged = inbox.dedupe_by_slug([page_a, page_b])
    assert len(merged) == 1
    # chosen = page_b (more sources), so its stability wins
    assert merged[0].stability == "stable"


def test_apply_v0_2_to_v0_3_migration_legacy_inbox_becomes_auto_promoted(tmp_path):
    # Simulate v0.2 state: single-source page with status=inbox + file in _inbox/
    state = _mkstate(**{
        "feedback/use-yarn": StateEntry(
            source_files=["bots/a/memory/feedback_use_yarn.md"],
            source_hash="sha256:oldhash",
            written_hash="sha256:wr1",
            written_at="2026-04-10T12:00:00",
            status="inbox",
            last_sync="2026-04-10T12:00:00",
        )
    })

    page_v2 = _page("use-yarn", sources=["bots/a/memory/feedback_use_yarn.md"])
    page_v2.source_hash = "sha256:newhash"

    result = inbox.apply_pages([page_v2], state, tmp_path, run_id="2026-04-13T12:00:00-run2")

    sacred = tmp_path / "shared" / "feedback" / "use-yarn.md"
    assert sacred.exists(), "v0.2→v0.3 migration should write to sacred dir"
    assert "auto-promoted" in sacred.read_text()
    entry = state.entries["feedback/use-yarn"]
    assert entry.status == "auto_promoted"
    assert result.auto_promoted == ["feedback/use-yarn"]
