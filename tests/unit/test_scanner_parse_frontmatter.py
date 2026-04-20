"""Public contract for parse_frontmatter — used by briefing picker + extractor."""
from __future__ import annotations

from mnemo.core.extract.scanner import parse_frontmatter


def test_parses_simple_frontmatter() -> None:
    text = "---\ntype: feedback\nname: foo\n---\n\nbody here\n"
    fm, body = parse_frontmatter(text)
    assert fm == {"type": "feedback", "name": "foo"}
    assert body == "\nbody here\n"


def test_returns_empty_when_no_frontmatter() -> None:
    text = "no frontmatter at all\nbody\n"
    fm, body = parse_frontmatter(text)
    assert fm == {}
    assert body == text


def test_handles_crlf_line_endings() -> None:
    text = "---\r\ntype: foo\r\n---\r\n\r\nbody\r\n"
    fm, body = parse_frontmatter(text)
    assert fm == {"type": "foo"}
    assert body == "\r\nbody\r\n"


def test_skips_lines_without_colon() -> None:
    text = "---\ntype: feedback\ngarbage line no colon\nname: bar\n---\nbody\n"
    fm, _ = parse_frontmatter(text)
    assert fm == {"type": "feedback", "name": "bar"}
