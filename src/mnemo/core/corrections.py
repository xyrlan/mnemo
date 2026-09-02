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
_SECTION_RE = re.compile(r"^## Corrections[ \t]*$", re.M)
_ITEM_RE = re.compile(
    r"""^\s*[-*]\s+["“](?P<quote>.+?)["”]\s*(?:→|->)\s*(?P<rule>.+?)\s*$"""
)
_QUOTE_CHARS = "\"'“”‘’"


@dataclass(frozen=True)
class Correction:
    quote: str
    rule: str


def normalize(text: str) -> str:
    """Whitespace-collapsed, dequoted, lower-cased form used for matching.

    ``.strip(_QUOTE_CHARS)`` only trims the ends on purpose: interior quotes
    are preserved, and both sides of a comparison go through this same
    normalisation so they line up regardless of which quote characters they
    were typed or rendered with.
    """
    return re.sub(r"\s+", " ", text).strip().strip(_QUOTE_CHARS).strip().lower()


def quote_matches_turn(quote: str, turn: str) -> bool:
    q = normalize(quote)
    return len(q) >= MIN_QUOTE_CHARS and q in normalize(turn)


# A keep quote needs at least this many non-stopword tokens to count as
# evidence of a rule rather than an approval (#119). Calibrated on the 61 keep
# verdicts of the saved real-vault plan: 54 survive at 5, 51 at 6, and the
# generic approvals the issue names are demoted at both. The spec allows 5
# and never lower.
MIN_CONTENT_TOKENS = 5

# Words that carry no rule content on their own. Portuguese and English:
# articles, pronouns, prepositions, auxiliaries, and the generic imperatives a
# user types to approve or nudge ("implementa os fixes", "yes do it").
_STOPWORDS = frozenset("""
a o os as um uma uns umas de do da dos das em no na nos nas por para pra pro com sem
e ou mas que se não nao sim ok okay ja já isso isto aqui ali lá la ele ela eles elas
eu tu voce você nós nos vc me te lhe meu minha seu sua
é e ser esta está estão estao foi era tem têm ter vai vão vamos vamo bora
pode podem poder deve devem faz faça fazer fez implementa implementar implemente
aplica aplicar aplique testa testar teste roda rodar rode
the a an and or but if so to of in on at for with by from as is are was were be been
do does did done it its this that these those i you we they he she me my your our
yes no ok okay please just go run apply implement test try let lets let's
""".split())


def quote_is_specific(quote: str) -> bool:
    """True when the quote has enough content words to establish a rule (#119).

    ``quote_matches_turn`` proves the user typed the words; it does not prove
    the words say anything. A one-line approval passes the substring check
    for every rule extracted from that session. Requiring ``MIN_CONTENT_TOKENS``
    non-stopword tokens rejects those without a cross-language lexical match
    against the rule (quotes are Portuguese, rules are English).
    """
    tokens = re.findall(r"\w+", normalize(quote))
    content = [t for t in tokens if t not in _STOPWORDS and len(t) > 1]
    return len(content) >= MIN_CONTENT_TOKENS


def _section_span(markdown: str) -> tuple[int, int] | None:
    m = _SECTION_RE.search(markdown)
    if m is None:
        return None
    start = m.start()
    # Section runs until the next "## " header or end of text.
    nxt = _HEADER_RE.search(markdown, m.end())
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
