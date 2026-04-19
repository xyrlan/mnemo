"""Shared text helpers: body_preview promoted from rule_activation."""
from __future__ import annotations

from mnemo.core.text_utils import body_preview


def test_body_preview_strips_frontmatter():
    text = "---\nname: x\ntags: []\n---\nActual body content here."
    assert body_preview(text, max_chars=300) == "Actual body content here."


def test_body_preview_truncates_at_whitespace_when_over_limit():
    text = "---\n---\n" + ("word " * 200).strip()
    preview = body_preview(text, max_chars=50)
    assert len(preview) <= 50
    # No mid-word cut: final char must be "word" boundary, not mid-"word".
    assert not preview.endswith("wor")
    assert not preview.endswith("wo")


def test_body_preview_returns_body_unchanged_when_short():
    assert body_preview("short", max_chars=300) == "short"
