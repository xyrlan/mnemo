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


# --- v0.4.1: anti-drift guardrail ---------------------------------------------


def test_bodies_similar_above_threshold():
    a = "React reconciles by key. Passing a new array reference just re-renders existing children; state survives."
    b = "React reconciles by key. Passing a new array reference to a .map just re-renders existing children; state survives."
    assert inbox._bodies_similar(a, b) is True


def test_bodies_similar_below_threshold():
    a = "React reconciles children by key prop, not by array reference identity."
    b = "Cache keys must include every upstream dependency version to avoid stale hits."
    assert inbox._bodies_similar(a, b) is False


def test_bodies_similar_handles_empty():
    assert inbox._bodies_similar("", "") is False
    assert inbox._bodies_similar("a b c", "") is False


def test_extract_body_strips_frontmatter():
    text = "---\nname: x\ntype: feedback\n---\n\nbody content here\n"
    assert inbox._extract_body(text) == "\nbody content here\n"


def test_extract_body_handles_no_frontmatter():
    assert inbox._extract_body("raw body") == "raw body"


def _mkstate(**entries):
    from mnemo.core.extract.scanner import ExtractionState
    return ExtractionState(
        last_run="2026-04-14T10:00:00",
        entries=dict(entries),
        schema_version=2,
    )


def test_guardrail_redirects_drifted_slug_to_existing(tmp_path):
    """Same source + similar body → LLM picked new slug → redirect to existing slug."""
    # Seed an existing auto-promoted page on disk + state entry
    existing_slug = "react-key-remount"
    existing_body = (
        "React reconciles children by key. Passing a new array reference to a "
        ".map just re-renders existing children; useState and useRef inside "
        "them survive. If you need to reset local state when upstream data "
        "changes, encode meaningful data into the key prop itself."
    )
    sacred = tmp_path / "shared" / "feedback" / f"{existing_slug}.md"
    sacred.parent.mkdir(parents=True)
    sacred.write_text(
        f"---\nname: r\ntype: feedback\ntags:\n  - auto-promoted\n  - react\n---\n\n{existing_body}\n"
    )
    state = _mkstate(**{
        f"feedback/{existing_slug}": StateEntry(
            source_files=["bots/sg-imports/memory/feedback_react_patterns.md"],
            source_hash="sha256:oldhash",
            written_hash="sha256:wr1",
            written_at="2026-04-14T10:00:00",
            status="auto_promoted",
            last_sync="2026-04-14T10:00:00",
        ),
    })

    # LLM re-extracts and picks a drifted slug with VERY similar body
    drifted = inbox.ExtractedPage(
        slug="react-key-remount-state-reset",  # ← drifted
        type="feedback",
        name="React key remount state reset",
        description="d",
        body=(
            "React reconciles children by key. Passing a new array reference "
            "to a .map just re-renders existing children; useState and useRef "
            "inside them survive. If you need to reset local state when "
            "upstream data changes, encode meaningful data into the key prop."
        ),
        source_files=["bots/sg-imports/memory/feedback_react_patterns.md"],
        source_hash="sha256:newhash",
        tags=["react"],
    )
    inbox.apply_pages([drifted], state, tmp_path, run_id="2026-04-14T11:00:00")

    # Guardrail should have redirected — no drifted file created
    assert not (tmp_path / "shared" / "feedback" / "react-key-remount-state-reset.md").exists()
    # Existing sacred file was updated in place
    assert sacred.exists()
    assert "react" in sacred.read_text()
    # State entry count unchanged (no new slug created)
    assert f"feedback/{existing_slug}" in state.entries
    assert "feedback/react-key-remount-state-reset" not in state.entries


def test_guardrail_does_not_redirect_distinct_rule_from_same_source(tmp_path):
    """One source file can legitimately produce multiple rules with disjoint bodies —
    guardrail must NOT collapse them."""
    sacred = tmp_path / "shared" / "feedback" / "react-key-remount.md"
    sacred.parent.mkdir(parents=True)
    sacred.write_text(
        "---\nname: r\ntype: feedback\ntags:\n  - auto-promoted\n  - react\n---\n\n"
        "React reconciles by key prop. Array reference changes do not remount children.\n"
    )
    state = _mkstate(**{
        "feedback/react-key-remount": StateEntry(
            source_files=["bots/sg-imports/memory/feedback_react_patterns.md"],
            source_hash="sha256:a",
            written_hash="sha256:wr1",
            written_at="2026-04-14T10:00:00",
            status="auto_promoted",
            last_sync="2026-04-14T10:00:00",
        ),
    })

    # Different rule extracted from the SAME source file (cache versioning,
    # not key-remount). Body tokens are disjoint.
    distinct = inbox.ExtractedPage(
        slug="react-cache-versioning",
        type="feedback",
        name="Cache keys must include full dependency versioning",
        description="d",
        body=(
            "Cache keys must include every upstream dependency version to "
            "avoid stale hits. Derived data loses integrity when a base input "
            "changes silently."
        ),
        source_files=["bots/sg-imports/memory/feedback_react_patterns.md"],
        source_hash="sha256:b",
        tags=["react", "caching"],
    )
    inbox.apply_pages([distinct], state, tmp_path, run_id="2026-04-14T11:00:00")

    # Both pages must coexist — distinct rules
    assert sacred.exists()
    assert (tmp_path / "shared" / "feedback" / "react-cache-versioning.md").exists()


def test_guardrail_does_not_redirect_different_source(tmp_path):
    """Same slug namespace but different source_files → definitely not drift."""
    sacred = tmp_path / "shared" / "feedback" / "existing.md"
    sacred.parent.mkdir(parents=True)
    sacred.write_text(
        "---\nname: e\ntype: feedback\n---\n\nidentical body text for comparison\n"
    )
    state = _mkstate(**{
        "feedback/existing": StateEntry(
            source_files=["bots/agent-a/memory/feedback_one.md"],
            source_hash="sha256:a",
            written_hash="sha256:wr1",
            written_at="2026-04-14T10:00:00",
            status="auto_promoted",
            last_sync="2026-04-14T10:00:00",
        ),
    })

    new_page = inbox.ExtractedPage(
        slug="brand-new",
        type="feedback",
        name="Different source",
        description="d",
        body="identical body text for comparison",  # same body but DIFFERENT source
        source_files=["bots/agent-b/memory/feedback_different.md"],
        source_hash="sha256:b",
    )
    inbox.apply_pages([new_page], state, tmp_path, run_id="2026-04-14T11:00:00")
    assert (tmp_path / "shared" / "feedback" / "brand-new.md").exists()
    assert sacred.exists()


def test_guardrail_skips_stale_state_entries(tmp_path):
    """State entry for a slug whose file was manually deleted must not block a
    new write under the same slug-space."""
    state = _mkstate(**{
        "feedback/deleted-slug": StateEntry(
            source_files=["bots/sg-imports/memory/feedback_x.md"],
            source_hash="sha256:a",
            written_hash="sha256:wr1",
            written_at="2026-04-14T10:00:00",
            status="auto_promoted",
            last_sync="2026-04-14T10:00:00",
        ),
    })
    # No file on disk for deleted-slug → stale entry

    new_page = inbox.ExtractedPage(
        slug="new-slug",
        type="feedback",
        name="new",
        description="d",
        body="some body text that would be similar enough to trigger guardrail",
        source_files=["bots/sg-imports/memory/feedback_x.md"],
        source_hash="sha256:b",
    )
    inbox.apply_pages([new_page], state, tmp_path, run_id="2026-04-14T11:00:00")
    # Stale entry ignored → new-slug written fresh, not redirected
    assert (tmp_path / "shared" / "feedback" / "new-slug.md").exists()


def test_guardrail_does_not_trigger_when_slugs_already_match(tmp_path):
    """Normal update flow (same slug picked again) must not misfire the guardrail."""
    sacred = tmp_path / "shared" / "feedback" / "stable-slug.md"
    sacred.parent.mkdir(parents=True)
    sacred.write_text(
        "---\nname: s\ntype: feedback\n---\n\nconsistent body for both runs\n"
    )
    state = _mkstate(**{
        "feedback/stable-slug": StateEntry(
            source_files=["bots/a/memory/feedback.md"],
            source_hash="sha256:old",
            written_hash="sha256:wr1",
            written_at="2026-04-14T10:00:00",
            status="auto_promoted",
            last_sync="2026-04-14T10:00:00",
        ),
    })
    updated = inbox.ExtractedPage(
        slug="stable-slug",  # same slug as before
        type="feedback",
        name="S", description="d",
        body="consistent body for both runs, slightly refined",
        source_files=["bots/a/memory/feedback.md"],
        source_hash="sha256:new",
    )
    result = inbox.apply_pages([updated], state, tmp_path, run_id="2026-04-14T11:00:00")
    # Update path fired, not drift redirect
    assert sacred.exists()


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
