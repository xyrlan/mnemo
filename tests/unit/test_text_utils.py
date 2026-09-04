"""Shared text helpers: body_preview promoted from rule_activation."""
from __future__ import annotations

from mnemo.core.text_utils import (
    GRAPH_SECTION_MARKER,
    body_preview,
    strip_advisory_notes,
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


ADVISORY = (
    "> _mnemo auto-promoter stripped an `enforce:` block from this rule._\n"
    "> _Review the pattern and re-add manually if safe._\n"
)


def test_strip_advisory_notes_removes_note_at_top():
    text = ADVISORY + "\nUse yarn in this repo.\n\n**Why:** lockfile.\n"
    assert strip_advisory_notes(text) == "Use yarn in this repo.\n\n**Why:** lockfile.\n"


def test_strip_advisory_notes_removes_note_at_bottom():
    text = "Use yarn in this repo.\n\n**Why:** lockfile.\n\n" + ADVISORY
    assert strip_advisory_notes(text) == "Use yarn in this repo.\n\n**Why:** lockfile.\n"


def test_strip_advisory_notes_unchanged_without_note():
    text = "Use yarn in this repo.\n\n> a real blockquote the user wrote\n"
    assert strip_advisory_notes(text) == text


def test_strip_advisory_notes_leaves_the_users_own_blockquote_alone():
    """Only the blockquote paragraph that opens with the mnemo prefix goes;
    a quote the user wrote in a later paragraph is rule content."""
    text = ADVISORY + "\nRule text.\n\n> the user's own quote\n"
    assert strip_advisory_notes(text) == "Rule text.\n\n> the user's own quote\n"


def test_strip_advisory_notes_idempotent():
    text = ADVISORY + "\nRule text.\n"
    once = strip_advisory_notes(text)
    assert strip_advisory_notes(once) == once


def test_strip_advisory_notes_collapses_blank_lines_left_behind():
    """A note between two paragraphs leaves one paragraph break, not three."""
    text = "First paragraph.\n\n" + ADVISORY + "\nSecond paragraph.\n"
    assert strip_advisory_notes(text) == "First paragraph.\n\nSecond paragraph.\n"


def test_body_preview_starts_at_rule_text_when_note_is_at_top():
    """Pages written before #134 carry the note above the rule; the preview
    handed to Claude must still start at the rule."""
    text = "---\nname: x\npromoted_without_enforce: true\n---\n\n" + ADVISORY + "\nUse yarn, never npm.\n"
    assert body_preview(text, max_chars=300) == "Use yarn, never npm."


def test_body_preview_drops_trailing_note_on_short_body():
    text = (
        "---\nname: x\n---\n\nUse yarn, never npm.\n\n" + ADVISORY
        + f"\n{GRAPH_SECTION_MARKER}\n## Sources\n- [[a]]\n"
    )
    assert body_preview(text, max_chars=300) == "Use yarn, never npm."
