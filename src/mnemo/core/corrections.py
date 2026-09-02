"""The ``## Corrections`` section of a session briefing.

A correction is the user telling Claude to stop, change, prefer, or
never/always do something. The briefing LLM proposes items as
``- "<verbatim quote>" → <rule>``; this module is the only reader and writer of
that format, and :func:`verify` is the mechanical check that the quote really
is a substring of something the user typed. A fabricated quote never reaches
disk.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SECTION_HEADER = "## Corrections"
ARROW = "→"
# Shorter than this and a quote matches by accident ("ok", "yes", "no").
MIN_QUOTE_CHARS = 12

_HEADER_RE = re.compile(r"^## ", re.M)
_ITEM_RE = re.compile(
    r"""^\s*[-*]\s+["“](?P<quote>.+?)["”]\s*(?:→|->)\s*(?P<rule>.+?)\s*$"""
)
_QUOTE_CHARS = "\"'“”‘’"


@dataclass(frozen=True)
class Correction:
    quote: str
    rule: str


def normalize(text: str) -> str:
    """Whitespace-collapsed, dequoted, lower-cased form used for matching."""
    return re.sub(r"\s+", " ", text).strip().strip(_QUOTE_CHARS).strip().lower()


def quote_matches_turn(quote: str, turn: str) -> bool:
    q = normalize(quote)
    return len(q) >= MIN_QUOTE_CHARS and q in normalize(turn)


def _section_span(markdown: str) -> tuple[int, int] | None:
    start = markdown.find(SECTION_HEADER)
    if start == -1:
        return None
    # Section runs until the next "## " header or end of text.
    nxt = _HEADER_RE.search(markdown, start + len(SECTION_HEADER))
    end = nxt.start() if nxt else len(markdown)
    return start, end


def parse_section(markdown: str) -> list[Correction]:
    span = _section_span(markdown)
    if span is None:
        return []
    out: list[Correction] = []
    for line in markdown[span[0]:span[1]].splitlines():
        m = _ITEM_RE.match(line)
        if m:
            out.append(Correction(quote=m.group("quote").strip(), rule=m.group("rule").strip()))
    return out


def verify(
    items: list[Correction], user_turns: list[str],
) -> tuple[list[Correction], list[Correction]]:
    """Split items into (kept, rejected) by whether the quote was really typed."""
    kept: list[Correction] = []
    rejected: list[Correction] = []
    for item in items:
        if any(quote_matches_turn(item.quote, t) for t in user_turns):
            kept.append(item)
        else:
            rejected.append(item)
    return kept, rejected


def render_section(items: list[Correction]) -> str:
    lines = [SECTION_HEADER]
    for it in items:
        lines.append(f'- "{it.quote}" {ARROW} {it.rule}')
    return "\n".join(lines) + "\n"


def strip_section(markdown: str) -> str:
    span = _section_span(markdown)
    if span is None:
        return markdown
    return (markdown[:span[0]].rstrip("\n") + "\n\n" + markdown[span[1]:].lstrip("\n")).strip("\n") + "\n"


def replace_section(markdown: str, items: list[Correction]) -> str:
    """Rewrite the section with exactly *items*; remove it when empty.

    Placed right after the ``## Decisions made`` section when present,
    otherwise appended at the end.
    """
    base = strip_section(markdown)
    if not items:
        return base
    block = render_section(items)
    anchor = base.find("## Decisions made")
    if anchor == -1:
        return base.rstrip("\n") + "\n\n" + block
    nxt = _HEADER_RE.search(base, anchor + 1)
    insert_at = nxt.start() if nxt else len(base)
    head = base[:insert_at].rstrip("\n") + "\n\n"
    tail = base[insert_at:]
    return head + block + ("\n" + tail if tail else "")
