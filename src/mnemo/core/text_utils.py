"""What retrieval strips from a rule page, and the preview it hands Claude.

A page on disk carries two things retrieval must never see — the graph
section (Obsidian wikilinks) and mnemo's maintainer-facing advisory notes.
:func:`retrieval_body` removes both; :func:`body_preview` builds the short
preview from the result.
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


# Opening of every blockquote paragraph mnemo writes INTO a rule body to talk
# to the maintainer (today: the auto-promoter's "stripped an ``enforce:``
# block" note, ``inbox/rendering._render_page``). Nothing else in the codebase
# writes it. Such notes stay on disk but must not reach Claude (#134): every
# retrieval, preview and export path strips them alongside the graph section.
ADVISORY_LINE_PREFIX = "> _mnemo "


def _is_advisory_opener(line: str) -> bool:
    return line.strip().startswith(ADVISORY_LINE_PREFIX)


def strip_advisory_notes(text: str) -> str:
    """Drop mnemo's maintainer-facing blockquote notes from a rule body.

    A note is one blockquote paragraph: a line whose stripped form starts
    with :data:`ADVISORY_LINE_PREFIX` plus the ``>`` lines that directly
    follow it — any ``>`` line beneath the note with no blank line in
    between is treated as part of the note, as markdown would render it.
    Each note goes together with the blank lines that flanked
    it, leaving one paragraph break where it sat between two paragraphs and
    none where it opened or closed the body. Returns *text* unchanged when
    no note is present. Idempotent.
    """
    if ADVISORY_LINE_PREFIX not in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not _is_advisory_opener(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        while out and not out[-1].strip():
            out.pop()
        i += 1
        while i < len(lines) and lines[i].strip().startswith(">"):
            i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        if out and i < len(lines):
            out.append("")
    result = "\n".join(out)
    if text.endswith("\n") and result and not result.endswith("\n"):
        result += "\n"
    return result


def retrieval_body(text: str) -> str:
    """The rule text as retrieval sees it: graph section and advisories gone.

    This is the single invariant behind every consumer of a rule body —
    BM25 tokenization, the reflex preview, ``mnemo export`` and the
    similarity index all read what this returns, while the on-disk page
    keeps both the graph section and the advisory notes (they exist for
    humans). *text* may be a whole page or just its body. Idempotent.
    """
    return strip_advisory_notes(strip_graph_section(text))


def body_preview(text: str, max_chars: int = 300) -> str:
    """Extract the first ~max_chars of a rule body, truncating on whitespace.

    Strips leading YAML frontmatter (between ``---\\n`` markers) and
    everything :func:`retrieval_body` strips, then returns either the full
    body (if short) or a whitespace-boundary truncation. The boundary rule prevents
    mid-word cuts like "implementat" — the returned slice ends at the last
    whitespace inside the first max_chars as long as that boundary is past
    the midpoint; otherwise returns the raw slice.
    """
    end = text.find("\n---\n", 4)
    body = text[end + 5:] if end != -1 else text
    body = retrieval_body(body).strip()
    if len(body) <= max_chars:
        return body
    truncated = body[:max_chars]
    last_ws = max(truncated.rfind(" "), truncated.rfind("\n"), truncated.rfind("\t"))
    if last_ws > max_chars // 2:
        return truncated[:last_ws]
    return truncated
