"""Shared text helpers used across the mnemo retrieval stack.

Moved from rule_activation._body_preview in v0.8 so multiple consumers
(rule_activation, reflex index builder) can share the same whitespace-
aware truncation without duplicating the logic.
"""
from __future__ import annotations


def body_preview(text: str, max_chars: int = 300) -> str:
    """Extract the first ~max_chars of a rule body, truncating on whitespace.

    Strips leading YAML frontmatter (between ``---\\n`` markers), then returns
    either the full body (if short) or a whitespace-boundary truncation. The
    boundary rule prevents mid-word cuts like "implementat" — the returned
    slice ends at the last whitespace inside the first max_chars as long as
    that boundary is past the midpoint; otherwise returns the raw slice.
    """
    end = text.find("\n---\n", 4)
    body = text[end + 5:].strip() if end != -1 else text.strip()
    if len(body) <= max_chars:
        return body
    truncated = body[:max_chars]
    last_ws = max(truncated.rfind(" "), truncated.rfind("\n"), truncated.rfind("\t"))
    if last_ws > max_chars // 2:
        return truncated[:last_ws]
    return truncated
