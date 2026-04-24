from mnemo.core.extract.inbox.rendering import _render_page
from mnemo.core.extract.inbox.types import ExtractedPage


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
