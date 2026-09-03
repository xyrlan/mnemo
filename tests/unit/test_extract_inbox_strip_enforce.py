from mnemo.core.extract.inbox.rendering import _render_page
from mnemo.core.extract.inbox.types import ExtractedPage
from mnemo.core.text_utils import GRAPH_SECTION_MARKER, body_preview


def _make_page_with_enforce() -> ExtractedPage:
    return ExtractedPage(
        slug="retarget-stacked-prs",
        source_hash="abc123",
        name="Retarget stacked PRs",
        description="Retarget child PRs",
        type="feedback",
        body="Never push without retargeting.",
        source_files=["bots/demo/briefings/sessions/abc.md"],
        tags=["git"],
        stability="stable",
        enforce={
            "tool": "Bash",
            "deny_command": "git push",
            "reason": "Check retarget before push",
        },
        activates_on=None,
    )


def test_auto_promoted_render_strips_enforce():
    page = _make_page_with_enforce()
    out = _render_page(page, run_id="2026-04-23T12:00:00", auto_promoted=True)
    # "enforce:" as a standalone frontmatter key starts at the beginning of a
    # line; "promoted_without_enforce:" also contains the substring "enforce:"
    # so we check for the newline-prefixed form to avoid false positives.
    assert "\nenforce:\n" not in out, "auto-promoted page must not carry enforce block"
    assert "promoted_without_enforce: true" in out
    assert "review" in out.lower()


def test_manual_render_preserves_enforce():
    page = _make_page_with_enforce()
    out = _render_page(page, run_id="2026-04-23T12:00:00", auto_promoted=False)
    assert "enforce:" in out
    assert "promoted_without_enforce" not in out


def test_auto_promoted_page_without_enforce_unchanged():
    page = _make_page_with_enforce()
    page = page.__class__(
        slug=page.slug,
        source_hash=page.source_hash,
        name=page.name,
        description=page.description,
        type=page.type,
        body=page.body,
        source_files=page.source_files,
        tags=page.tags,
        stability=page.stability,
        enforce=None,
        activates_on=page.activates_on,
    )
    out = _render_page(page, run_id="2026-04-23T12:00:00", auto_promoted=True)
    assert "promoted_without_enforce" not in out


def test_auto_promoted_advisory_follows_the_rule_text():
    """#134: the advisory is maintainer noise. It goes after the rule body
    (before the Sources section) so previews start at the rule, and it no
    longer points at a docs path that does not exist in user checkouts."""
    page = _make_page_with_enforce()
    out = _render_page(page, run_id="2026-04-23T12:00:00", auto_promoted=True)

    body = out.split("\n---\n", 1)[1]
    assert body.startswith("\nNever push without retargeting.\n")
    assert "promoted_without_enforce: true" in out
    assert "docs/superpowers" not in out

    rule_at = out.index("Never push without retargeting.")
    note_at = out.index("> _mnemo auto-promoter stripped an `enforce:` block from this rule._")
    marker_at = out.index(GRAPH_SECTION_MARKER)
    assert rule_at < note_at < marker_at
    assert "> _Review the pattern and re-add manually if safe._\n" in out
    assert body_preview(out) == "Never push without retargeting."
