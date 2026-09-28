"""The text of a reflex injection (#542): the full body #535 measured, held
under Claude Code's persist limit (#533)."""
from __future__ import annotations

from mnemo.core.hook_envelope import ENVELOPE_MAX_BYTES
from mnemo.core.reflex import render
from mnemo.core.reflex.render import Entry

PAGE = ("---\nname: use-yarn\ndescription: d\n---\n"
        "Use yarn, never npm.\n\n**Why:** the lockfile is yarn's.\n")


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def _body(n_lines: int, word: str = "line") -> str:
    return "\n".join(f"- {word} {i:04d} of a long rule body, long enough to count" for i in range(n_lines))


def test_full_body_drops_the_frontmatter_and_cuts_nothing():
    long = PAGE + "x " * 400
    assert render.full_body(long).startswith("Use yarn, never npm.\n\n**Why:**")
    assert render.full_body(long).endswith("x")
    assert "name:" not in render.full_body(long)


def test_a_whole_body_goes_under_its_head_without_the_suffix():
    body = render.full_body(PAGE)
    out = render.render([Entry("use-yarn", "Use yarn, never npm.", body, "/v/p.md")])
    assert out.text == ("mnemo reflex context:\n"
                        "• [[use-yarn]]:\nUse yarn, never npm.\n\n**Why:** the lockfile is yarn's.")
    assert render.READ_SUFFIX not in out.text
    assert out.rule_whole == [True]
    assert out.rule_bytes == [_size(out.text) - _size("mnemo reflex context:\n")]


def test_the_preview_format_is_the_line_shipped_before_byte_for_byte():
    preview = "Use yarn,\nnever npm."
    out = render.render([Entry("use-yarn", preview, "Use yarn,\nnever npm. And more.")], render.PREVIEW)
    assert out.text == ("mnemo reflex context:\n• [[use-yarn]]: Use yarn, never npm. "
                        "(call read_mnemo_rule if you need the full file).")
    assert out.rule_whole == [False]
    short = render.render([Entry("a", "Short rule.", "Short rule.")], render.PREVIEW)
    assert short.rule_whole == [True]


def test_an_unreadable_page_goes_out_as_its_preview():
    out = render.render([Entry("gone", "The preview.", None)])
    assert out.text.endswith("• [[gone]]: The preview. (call read_mnemo_rule if you need the full file).")
    assert out.rule_whole == [False]


def test_the_format_reads_anything_unknown_as_full():
    assert render.body_format(None) == "full"
    assert render.body_format({}) == "full"
    assert render.body_format({"body": "Preview"}) == "preview"
    assert render.body_format({"body": "bogus"}) == "full"


def test_fair_shares_give_short_rules_their_whole_size():
    assert render.fair_shares([100, 5000, 5000], 6100) == [100, 3000, 3000]
    assert render.fair_shares([100, 200], 1000) == [100, 200]
    assert render.fair_shares([9000, 9000, 9000], 9000) == [3000, 3000, 3000]


def test_bodies_that_fit_are_never_cut():
    entries = [Entry(s, "p", _body(40, s), f"/v/{s}.md") for s in ("a", "b")]
    out = render.render(entries)
    assert out.rule_whole == [True, True]
    assert _size(out.text) <= ENVELOPE_MAX_BYTES


def test_an_over_long_body_is_cut_at_a_line_break_and_names_its_page():
    short = Entry("short", "p", "A short rule.", "/v/short.md")
    long = Entry("long", "p", _body(400), "/v/long.md")
    out = render.render([short, long])
    assert _size(out.text) <= ENVELOPE_MAX_BYTES
    assert out.rule_whole == [True, False]
    entry = out.text.split("\n• [[long]]:\n", 1)[1]
    kept, tail = entry.rsplit("\n", 1)
    assert tail == ("[cut to fit the prompt limit — the full rule is at /v/long.md] "
                    "(call read_mnemo_rule if you need the full file).")
    # The cut falls on a whole line of the body, and the short rule's leftover
    # went to the long one.
    assert kept.split("\n")[-1] in _body(400).split("\n")
    assert _size(out.text) > ENVELOPE_MAX_BYTES - 100


def test_three_of_the_largest_bodies_stay_under_the_cap_and_share_it_fairly():
    entries = [Entry(s, "p", _body(200, s), f"/v/{s}.md") for s in ("a", "b", "c")]
    out = render.render(entries)
    assert _size(out.text) <= ENVELOPE_MAX_BYTES
    assert out.rule_whole == [False, False, False]
    assert max(out.rule_bytes) - min(out.rule_bytes) < 100


def test_a_body_with_no_line_break_is_cut_on_a_word():
    out = render.render([Entry("one", "p", "word " * 3000, "/v/one.md")])
    assert _size(out.text) <= ENVELOPE_MAX_BYTES
    kept = out.text.split("• [[one]]:\n", 1)[1].rsplit("\n", 1)[0]
    assert kept.endswith("word") and set(kept.split()) == {"word"}


def test_a_preview_line_is_paid_for_before_the_bodies_are_shared():
    entries = [Entry("gone", "x" * 300, None), Entry("long", "p", _body(400), "/v/long.md")]
    out = render.render(entries)
    assert _size(out.text) <= ENVELOPE_MAX_BYTES
    assert out.rule_whole == [False, False]
    assert "• [[gone]]: " + "x" * 300 in out.text
