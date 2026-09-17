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


def test_section_header_must_be_a_whole_line():
    body = "## TL;DR\nsee the ## Corrections section below\n\n## Dead ends\n- x\n"
    assert C.parse_section(body) == []
    assert C.strip_section(body) == body


def test_longer_header_is_not_the_section():
    body = '## Corrections to the plan\n- "use yarn not npm" → Use yarn\n'
    assert C.parse_section(body) == []


def test_quote_is_specific_treats_unaccented_vao_as_a_stopword():
    """Users type "vao" for "vão" as often as not; both must count as filler (#119)."""
    from mnemo.core.corrections import quote_is_specific
    assert not quote_is_specific("vão subir o deploy do backend hoje")
    assert not quote_is_specific("vao subir o deploy do backend hoje")


# --- the dispatch brief is not a correction (#244) ------------------------------
#
# A correction reacts to something the assistant did. On the real vault 23 of
# 64 Corrections items quoted only the first turn, 21 of them in dispatched
# children whose sole user turn is mnemo's own dispatch template ("Do NOT merge
# or push without asking."). That template is skipped; a human's first message
# is not — the README's five-minute loop is a one-turn correction.

def test_verify_rejects_a_quote_found_only_in_the_dispatch_brief():
    items = [C.Correction(quote="do not merge or push without asking", rule="Never merge unasked")]
    kept, rejected = C.verify(items, ["Work on issue #1 in this repo: x\n\nDo not merge or push without asking.", "looks good"])
    assert kept == [] and rejected == items


def test_verify_rejects_the_dispatch_brief_even_as_the_only_turn():
    items = [C.Correction(quote="write commits and any PR in English", rule="English commits")]
    kept, rejected = C.verify(items, ["Work on issue #7 in this repo: y\n\nWrite commits and any PR in English."])
    assert kept == [] and rejected == items


def test_verify_rejects_a_contract_piece_brief_too():
    items = [C.Correction(quote="do not merge or push without asking", rule="Never merge unasked")]
    kept, rejected = C.verify(items, ['You are building one piece of the feature "f": p\n\nDo not merge or push without asking.'])
    assert kept == [] and rejected == items


def test_verify_keeps_a_rule_a_human_typed_as_the_first_and_only_turn():
    """The README's five-minute loop: one session, one line, `mnemo learn`."""
    items = [C.Correction(quote="never use npm in this repo, always yarn", rule="Use yarn, never npm")]
    kept, rejected = C.verify(items, ["never use npm in this repo, always yarn"])
    assert kept == items and rejected == []


def test_verify_keeps_a_quote_the_user_repeated_after_the_dispatch_brief():
    items = [C.Correction(quote="never retry on 4xx, only on 5xx", rule="Retry only 5xx")]
    kept, _ = C.verify(items, [
        "Work on issue #2 in this repo: retries\n\nnever retry on 4xx, only on 5xx",
        "I said never retry on 4xx, only on 5xx — fix it",
    ])
    assert kept == items


def test_is_dispatch_brief_matches_only_mnemo_openings():
    assert C.is_dispatch_brief("Work on issue #12 in this repo: t")
    assert C.is_dispatch_brief('  You are building one piece of the feature "f": s')
    assert not C.is_dispatch_brief("never use npm in this repo, always yarn")
    assert not C.is_dispatch_brief("")


def test_is_job_scratch_matches_a_background_jobs_scratch_dir(monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    # The two sessions #348 found linked to a live rule, and one that moved into a tree.
    assert C.is_job_scratch("/Users/x/.claude/jobs/d8d44ec2/tmp/probe86")
    assert C.is_job_scratch("/Users/x/.claude/jobs/d8d44ec2/tmp")
    assert C.is_job_scratch("/Users/x/.claude/jobs/d8d44ec2/tmp/probe86/.claude/worktrees/probe-86-task")
    assert C.is_job_scratch(r"C:\Users\x\.claude\jobs\d8d44ec2\tmp\probe")


def test_is_job_scratch_leaves_real_work_alone(monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    # The five-minute loop's real correction was typed in a system temp dir.
    assert not C.is_job_scratch("/private/tmp/mnemo-demo/app")
    # A removed dispatch tree is gone, not scratch.
    assert not C.is_job_scratch("/Users/x/github/mnemo-wt-158")
    assert not C.is_job_scratch("/Users/x/github/mnemo/.claude/worktrees/fix-1")
    # The job dir itself, and a project merely named like one.
    assert not C.is_job_scratch("/Users/x/.claude/jobs/d8d44ec2")
    assert not C.is_job_scratch("/Users/x/.claude/jobs/d8d44ec2/tmpfiles")
    assert not C.is_job_scratch("/Users/x/code/jobs/abc/tmp")
    assert not C.is_job_scratch("")


def test_is_job_scratch_honours_a_custom_config_dir(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/opt/claude-home/")
    assert C.is_job_scratch("/opt/claude-home/jobs/1f87be87/tmp/probe")
    assert not C.is_job_scratch("/opt/claude-home/jobs/1f87be87/out")
    assert not C.is_job_scratch("/opt/claude-homer/jobs/1f87be87/tmp")


# --- #360: a `!` shell-mode turn is not the user's words ------------------------

SHELL_MERGE = "<bash-input> gh pr merge 186 --squash --delete-branch --admin</bash-input>"


def test_is_shell_turn_matches_the_three_shell_mode_blocks_only():
    assert C.is_shell_turn(SHELL_MERGE)
    assert C.is_shell_turn("  <bash-stdout>merged</bash-stdout><bash-stderr></bash-stderr>")
    assert C.is_shell_turn("<bash-stderr>fatal: no upstream</bash-stderr>")
    assert not C.is_shell_turn("pode abrir o merge com gh pr merge --admin")
    assert not C.is_shell_turn("why did <bash-input> show up in the briefing?")
    assert not C.is_shell_turn("")


def test_verify_rejects_a_quote_found_only_in_a_shell_command():
    items = [C.Correction(quote=SHELL_MERGE, rule="Merge with --admin")]
    kept, rejected = C.verify(items, ["open the PR", SHELL_MERGE])
    assert kept == [] and rejected == items


def test_verify_rejects_a_quote_found_only_in_shell_output():
    out = "<bash-stdout>never retry on 4xx, only on 5xx</bash-stdout><bash-stderr></bash-stderr>"
    items = [C.Correction(quote="never retry on 4xx, only on 5xx", rule="r")]
    assert C.verify(items, ["add a retry helper", out]) == ([], items)


def test_verify_keeps_a_quote_the_user_also_typed_outside_the_shell():
    quote = "gh pr merge 186 --squash --delete-branch --admin"
    items = [C.Correction(quote=quote, rule="r")]
    turns = [SHELL_MERGE, "just run gh pr merge 186 --squash --delete-branch --admin yourself"]
    assert C.verify(items, turns) == (items, [])


def test_verify_skips_shell_turns_after_a_dispatch_brief_too():
    items = [C.Correction(quote=SHELL_MERGE, rule="r")]
    turns = ["Work on issue #12 in this repo: x", SHELL_MERGE]
    assert C.verify(items, turns) == ([], items)
