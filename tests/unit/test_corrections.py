"""core/corrections — the ## Corrections section is only ever the user's words."""
from __future__ import annotations

from mnemo.core import corrections as C

BODY = """## TL;DR
Did stuff.

## Decisions made
- Used axios. **Why:** already a dependency.

## Corrections
- "never retry on 4xx, only on 5xx" → Retry only 5xx responses
- "use   YARN  not npm" → Use yarn for package management
- "this quote was invented" → Invented rule
- not a quoted item at all

## Dead ends
- tried fetch.
"""

TURNS = [
    "add a retry helper",
    "no — never retry on 4xx, only on 5xx. and use yarn not npm",
]


def test_parse_section_reads_quoted_items_only():
    items = C.parse_section(BODY)
    assert [i.quote for i in items] == [
        "never retry on 4xx, only on 5xx",
        "use   YARN  not npm",
        "this quote was invented",
    ]
    assert items[0].rule == "Retry only 5xx responses"


def test_parse_section_absent_returns_empty():
    assert C.parse_section("## TL;DR\nnothing\n") == []


def test_verify_keeps_substring_matches_case_and_space_insensitive():
    kept, rejected = C.verify(C.parse_section(BODY), TURNS)
    assert [k.quote for k in kept] == [
        "never retry on 4xx, only on 5xx",
        "use   YARN  not npm",
    ]
    assert [r.quote for r in rejected] == ["this quote was invented"]


def test_verify_rejects_quotes_too_short_to_mean_anything():
    items = [C.Correction(quote="ok", rule="Say ok")]
    kept, rejected = C.verify(items, ["ok then"])
    assert kept == [] and rejected == items


def test_quote_matches_turn_normalises_curly_quotes_and_whitespace():
    assert C.quote_matches_turn("“Use  yarn not npm”", "use yarn not npm")
    assert not C.quote_matches_turn("use pnpm instead", "use yarn not npm")


def test_replace_section_rewrites_only_verified_items_after_decisions():
    kept, _ = C.verify(C.parse_section(BODY), TURNS)
    out = C.replace_section(BODY, kept)
    assert "this quote was invented" not in out
    assert out.index("## Decisions made") < out.index("## Corrections") < out.index("## Dead ends")
    assert '- "never retry on 4xx, only on 5xx" → Retry only 5xx responses' in out


def test_replace_section_with_no_items_removes_the_section():
    out = C.replace_section(BODY, [])
    assert "## Corrections" not in out
    assert "## Dead ends" in out


def test_replace_section_appends_when_no_decisions_header():
    body = "## TL;DR\nx\n"
    out = C.replace_section(body, [C.Correction(quote="use yarn not npm", rule="Use yarn")])
    assert out.rstrip().endswith('- "use yarn not npm" → Use yarn')
