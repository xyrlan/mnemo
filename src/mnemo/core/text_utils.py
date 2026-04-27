"""Shared text helpers used across the mnemo retrieval stack.

Moved from rule_activation._body_preview in v0.8 so multiple consumers
(rule_activation, reflex index builder) can share the same whitespace-
aware truncation without duplicating the logic.
"""
from __future__ import annotations

# Marker that bookends an optional ``## Sources`` section appended by
# ``inbox/rendering._render_page``. The section contains Obsidian wikilinks
# pointing at the briefings that produced each rule — purely additive
# rendering for graph navigation in any markdown viewer (Obsidian, GitHub).
# All retrieval paths (BM25F tokenization, preview generation, recall
# harness) MUST strip everything from the marker onward before indexing,
# so the wikilinks do not pollute scoring or leak into Claude's context.
GRAPH_SECTION_MARKER = "<!-- mnemo:graph-section -->"


def strip_graph_section(text: str) -> str:
    """Drop the optional graph-edges section appended at the end of a rule body.

    Returns *text* unchanged when the marker is absent. Idempotent.
    """
    idx = text.find(GRAPH_SECTION_MARKER)
    if idx == -1:
        return text
    return text[:idx].rstrip() + "\n"


def body_preview(text: str, max_chars: int = 300) -> str:
    """Extract the first ~max_chars of a rule body, truncating on whitespace.

    Strips leading YAML frontmatter (between ``---\\n`` markers) AND the
    trailing graph-section marker, then returns either the full body (if
    short) or a whitespace-boundary truncation. The boundary rule prevents
    mid-word cuts like "implementat" — the returned slice ends at the last
    whitespace inside the first max_chars as long as that boundary is past
    the midpoint; otherwise returns the raw slice.
    """
    end = text.find("\n---\n", 4)
    body = text[end + 5:] if end != -1 else text
    body = strip_graph_section(body).strip()
    if len(body) <= max_chars:
        return body
    truncated = body[:max_chars]
    last_ws = max(truncated.rfind(" "), truncated.rfind("\n"), truncated.rfind("\t"))
    if last_ws > max_chars // 2:
        return truncated[:last_ws]
    return truncated
