"""Shared text helpers: body_preview promoted from rule_activation."""
from __future__ import annotations

from mnemo.core.text_utils import (
    GRAPH_SECTION_MARKER,
    body_preview,
    strip_graph_section,
)


def test_body_preview_strips_frontmatter():
    text = "---\nname: x\ntags: []\n---\nActual body content here."
    assert body_preview(text, max_chars=300) == "Actual body content here."


def test_body_preview_strips_graph_section():
    """Wikilinks appended for Obsidian must not leak into the preview shown
    to Claude or scored by retrieval (regression for 2026-04-27 graph feature)."""
    text = (
        "---\nname: x\n---\n"
        "Actual body content.\n"
        f"\n{GRAPH_SECTION_MARKER}\n"
        "## Sources\n"
        "- [[bots/foo/briefings/sessions/abc]]\n"
    )
    preview = body_preview(text, max_chars=300)
    assert "## Sources" not in preview
    assert "[[bots" not in preview
    assert "Actual body content." in preview


def test_strip_graph_section_idempotent_when_marker_absent():
    plain = "no marker here.\n"
    assert strip_graph_section(plain) == plain


def test_strip_graph_section_removes_everything_after_marker():
    text = (
        "real body\n"
        f"\n{GRAPH_SECTION_MARKER}\n"
        "## Sources\n- [[a]]\n- [[b]]\n"
    )
    out = strip_graph_section(text)
    assert "Sources" not in out
    assert "[[" not in out
    assert "real body" in out


def test_body_preview_truncates_at_whitespace_when_over_limit():
    text = "---\n---\n" + ("word " * 200).strip()
    preview = body_preview(text, max_chars=50)
    assert len(preview) <= 50
    # No mid-word cut: final char must be "word" boundary, not mid-"word".
    assert not preview.endswith("wor")
    assert not preview.endswith("wo")


def test_body_preview_returns_body_unchanged_when_short():
    assert body_preview("short", max_chars=300) == "short"
