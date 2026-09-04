from __future__ import annotations

from mnemo.core.export.select import ExportRule


def _rule(slug="use-yarn-not-npm", **kw):
    base = dict(
        slug=slug, name="Use yarn, never npm", body="Use yarn in this repo.\n",
        quote="never use npm in this repo, always yarn", universal=False,
        source_count=1, page_type="feedback",
    )
    base.update(kw)
    return ExportRule(**base)


def test_entry_has_heading_body_and_quote():
    from mnemo.core.export.render import render_entry

    text = render_entry(_rule())
    assert text.startswith("### Use yarn, never npm  `use-yarn-not-npm`\n")
    assert "Use yarn in this repo.\n" in text
    assert text.rstrip().endswith('> you said: "never use npm in this repo, always yarn"')


def test_entry_without_quote_has_no_you_said_line():
    from mnemo.core.export.render import render_entry

    assert "you said" not in render_entry(_rule(quote=None))


def test_universal_marker_after_slug():
    from mnemo.core.export.render import render_entry

    assert render_entry(_rule(universal=True)).startswith(
        "### Use yarn, never npm  `use-yarn-not-npm` (universal)\n"
    )


def test_block_wraps_entries_in_markers_with_counts():
    from mnemo.core.export.render import END_MARKER, START_MARKER, render_block

    block = render_block([_rule(universal=True), _rule(slug="two", universal=False)],
                         project="app", today="2026-09-02")
    lines = block.splitlines()
    assert lines[0].startswith(START_MARKER)
    assert "2026-09-02" in lines[0] and "edit the vault, not this block" in lines[0]
    assert lines[1] == ""
    assert lines[2] == "## Rules mnemo learned from you (project: app, 2 rules, 1 universal)"
    assert lines[-2] == ""
    assert lines[-1] == END_MARKER
    assert block.count("### ") == 2


def test_marker_injection_in_body_is_neutralised():
    from mnemo.core.export.render import END_MARKER, render_block, render_entry

    rule = _rule(body="See <!-- mnemo:end --> here.\n")
    entry = render_entry(rule)
    assert "<!-- mnemo:end -->" not in entry

    block = render_block([rule], project="app", today="2026-09-02")
    assert block.count(END_MARKER) == 1


def test_block_spacing_has_blank_lines_around_title_and_before_end():
    from mnemo.core.export.render import render_block

    block = render_block([_rule()], project="app", today="2026-09-02")
    lines = block.splitlines()
    assert lines[1] == ""
    assert lines[3] == ""
    assert lines[-2] == ""


def test_empty_body_entry_has_no_trailing_blank_paragraph():
    from mnemo.core.export.render import render_entry

    text = render_entry(_rule(body="", quote=None))
    assert text == "### Use yarn, never npm  `use-yarn-not-npm`\n"


def test_empty_rules_block_has_no_stray_blank_lines():
    from mnemo.core.export.render import END_MARKER, render_block

    block = render_block([], project="app", today="2026-09-02")
    lines = block.splitlines()
    assert lines[-2] == ""
    assert lines[-1] == END_MARKER
    assert "### " not in block


def test_block_title_uses_singular_for_one_rule():
    from mnemo.core.export.render import render_block

    block = render_block([_rule()], project="app", today="2026-09-02")
    assert "1 rule," in block
    assert "1 rules," not in block


def test_backtick_in_name_is_neutralised():
    from mnemo.core.export.render import render_entry

    text = render_entry(_rule(name="Use `yarn`, never npm"))
    assert "`yarn`" not in text
    assert text.startswith("### Use 'yarn', never npm  `use-yarn-not-npm`\n")


def test_entry_hash_is_stable_and_content_sensitive():
    from mnemo.core.export.render import entry_hash

    assert entry_hash(_rule()) == entry_hash(_rule())
    assert entry_hash(_rule()) != entry_hash(_rule(body="Other.\n"))
    assert len(entry_hash(_rule())) == 64


def test_estimated_tokens_is_chars_over_four():
    from mnemo.core.export.render import TOKEN_WARN, estimated_tokens

    assert estimated_tokens("a" * 400) == 100
    assert TOKEN_WARN == 4000


# --- compact format (#129) -------------------------------------------------

_LONG_BODY = (
    "Use yarn in this repo.\n"
    "\n"
    "**Why:** npm rewrites the lockfile and CI diffs it.\n"
    "\n"
    "**How to apply:** run `yarn add`, never `npm install`.\n"
)


def test_first_paragraph_stops_at_the_first_blank_line():
    from mnemo.core.export.render import first_paragraph

    assert first_paragraph(_LONG_BODY) == "Use yarn in this repo."


def test_first_paragraph_treats_a_whitespace_only_line_as_blank():
    from mnemo.core.export.render import first_paragraph

    assert first_paragraph("One.\n  \t\nTwo.\n") == "One."


def test_first_paragraph_keeps_a_multi_line_paragraph_whole():
    from mnemo.core.export.render import first_paragraph

    assert first_paragraph("- a\n- b\n- c\n\nrest\n") == "- a\n- b\n- c"


def test_first_paragraph_of_a_single_paragraph_body_is_the_body():
    from mnemo.core.export.render import first_paragraph

    assert first_paragraph("\n\nJust this.\n") == "Just this."
    assert first_paragraph("") == ""


def test_entry_is_compact_by_default_and_full_on_request():
    from mnemo.core.export.render import render_entry

    compact = render_entry(_rule(body=_LONG_BODY))
    assert compact == (
        "### Use yarn, never npm  `use-yarn-not-npm`\n"
        "Use yarn in this repo.\n"
        '> you said: "never use npm in this repo, always yarn"\n'
    )
    full = render_entry(_rule(body=_LONG_BODY), full=True)
    assert "**Why:**" in full and "**How to apply:**" in full
    assert full.rstrip().endswith('> you said: "never use npm in this repo, always yarn"')


def test_compact_entry_still_neutralises_markers_in_the_first_paragraph():
    from mnemo.core.export.render import render_entry

    text = render_entry(_rule(body="See <!-- mnemo:end --> here.\n\n**Why:** x\n"))
    assert "<!-- mnemo:end -->" not in text and "here." in text


def test_compact_block_points_at_the_full_rule_and_full_block_does_not():
    from mnemo.core.export.render import render_block

    compact = render_block([_rule(body=_LONG_BODY)], project="app", today="2026-09-03")
    lines = compact.splitlines()
    assert lines[2].startswith("## Rules mnemo learned from you")
    assert lines[3] == ""
    assert "read_mnemo_rule" in lines[4] and "mnemo export --full" in lines[4]
    assert lines[5] == ""
    assert lines[6].startswith("### ")
    assert "**Why:**" not in compact

    full = render_block([_rule(body=_LONG_BODY)], project="app", today="2026-09-03", full=True)
    assert "read_mnemo_rule" not in full and "**Why:**" in full
    assert full.splitlines()[4].startswith("### ")


def test_empty_compact_block_has_no_pointer_line():
    from mnemo.core.export.render import render_block

    assert "read_mnemo_rule" not in render_block([], project="app", today="2026-09-03")


def test_entry_hash_follows_the_format():
    from mnemo.core.export.render import entry_hash

    rule = _rule(body=_LONG_BODY)
    assert entry_hash(rule) == entry_hash(rule, full=False)
    assert entry_hash(rule) != entry_hash(rule, full=True)
    # A change confined to the Why paragraph leaves the compact hash alone.
    other = _rule(body=_LONG_BODY.replace("CI diffs it", "CI rejects it"))
    assert entry_hash(rule) == entry_hash(other)
    assert entry_hash(rule, full=True) != entry_hash(other, full=True)
