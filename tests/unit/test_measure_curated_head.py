"""``tools/measure_curated_head.py`` over hand-built notes, indexes and units
whose answers are known by construction (#633)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_curated_head as tool  # noqa: E402

NOTE = "---\nname: Merge with admin\ndescription: Run gh pr merge --admin yourself\nmetadata:\n  type: feedback\n---\n\nBody first. Second.\n"
BARE = "---\nname: bare\n---\n\nThe body leads. Then more.\n"


# --- entries -----------------------------------------------------------------------------

def test_a_note_becomes_one_line_led_by_its_description_else_its_first_sentence():
    assert tool.note_entry("merge.md", NOTE)["line"] == "- [Merge with admin](merge.md) — Run gh pr merge --admin yourself"
    assert tool.note_entry("bare.md", BARE)["line"] == "- [bare](bare.md) — The body leads."
    assert tool.note_entry("x.md", "no frontmatter at all")["line"] == "- [x](x.md) — no frontmatter at all"


def test_index_lines_keep_the_record_then_the_dated_lines_it_lacked_and_drop_non_entries():
    recorded = "# Memory\n\n- [A](a.md) — a\n- [B](b.md) — b\n> WARNING: MEMORY.md is 300 lines (limit: 200)."
    reconstructed = "- [A](a.md) — a\n- [C](c.md) — c"
    assert tool.index_lines(recorded, reconstructed) == ["- [A](a.md) — a", "- [B](b.md) — b", "- [C](c.md) — c"]


def test_native_entries_follow_the_index_then_unindexed_notes_newest_first():
    notes = {"a.md": NOTE, "b.md": BARE, "old.md": BARE, "new.md": BARE}
    born = {"a.md": 1.0, "b.md": 2.0, "old.md": 3.0, "new.md": 4.0}
    index = ["- [B](b.md) · [A](a.md)", "- a fact with no link", "- [Gone](gone.md) — deleted since"]
    got = tool.native_entries(notes, born, index)
    assert [e["key"] for e in got] == ["note:b.md", "note:a.md", "line:1", "line:2", "note:new.md", "note:old.md"]
    assert got[2]["line"] == "- a fact with no link"
    assert got[3]["line"] == "- [Gone](gone.md) — deleted since"   # a note gone today keeps its line


def test_rank_puts_evidence_first_and_keeps_native_order_on_ties():
    entries = [{"key": "note:a.md", "file": "a.md", "pos": 0}, {"key": "note:b.md", "file": "b.md", "pos": 1},
               {"key": "line:2", "file": None, "pos": 2}, {"key": "rule:r", "slugs": ["r"], "file": None, "pos": 3}]
    score = tool.scorer({"rb": 2, "r": 1, "ra": 0}, {"a.md": {"ra"}, "b.md": {"rb", "zz"}})
    assert [e["key"] for e in tool.rank(entries, score)] == ["note:b.md", "rule:r", "note:a.md", "line:2"]


def test_fit_holds_the_head_to_both_limits_and_tries_the_next_entry():
    entries = [{"line": "x" * 10}, {"line": "y" * 30}, {"line": "z" * 5}, {"line": "w" * 5}]
    kept, out = tool.fit(entries, line_limit=3, char_limit=20)
    assert [e["line"][0] for e in kept] == ["x", "z"]          # 10 + 1 + 5 = 16; w would make 22
    assert [e["line"][0] for e in out] == ["y", "w"]
    kept, out = tool.fit(entries[2:] * 3, line_limit=2, char_limit=100)
    assert len(kept) == 2 and len(out) == 4
    head = tool.head_text(kept)
    assert tool.loaded_lines(head, line_limit=2, char_limit=100) == 2


def test_swap_memory_replaces_only_the_index_and_adds_one_where_none_loaded():
    files = [("/h/.claude/CLAUDE.md", "global"), ("/p/memory/MEMORY.md", "old head")]
    assert tool.swap_memory(files, "new head", "L") == [("/h/.claude/CLAUDE.md", "global"), ("/p/memory/MEMORY.md", "new head")]
    assert tool.swap_memory(files[:1], "new head", "L") == [("/h/.claude/CLAUDE.md", "global"), ("L", "new head")]
    assert tool.swap_memory(files[:1], "", "L") == files[:1]


def test_unchanged_texts_hash_to_the_baseline_ids():
    files = [("CLAUDE.md", "c"), ("MEMORY.md", "m")]
    assert tool.text_ids("s", "ss", files, "r") == tool.text_ids("s", "ss", tool.swap_memory(files, "m", "L"), "r")
    assert tool.text_ids("s", "ss", files, "r") != tool.text_ids("s", "ss", tool.swap_memory(files, "m2", "L"), "r")


def test_counts_before_never_count_the_session_itself_or_later_ones():
    events = {"r": [("s1", 10.0), ("s2", 20.0), ("me", 5.0)], "q": [("s3", 50.0)]}
    assert tool.counts_before(events, "me", 30.0) == {"r": 2}

    class Ledger:
        def picks_before(self, project, t, sid):
            return {"r": 1} if (project, t, sid) == (None, 30.0, "me") else {}

    assert tool.counts_before(Ledger(), "me", 30.0) == {"r": 1}


def test_loaded_names_read_only_the_loaded_part_of_the_index():
    long = "\n".join(["- [n%d](n%d.md)" % (i, i) for i in range(300)])
    names = tool.loaded_names([("x/MEMORY.md", long), ("CLAUDE.md", "[c](c.md)")])
    assert "n0.md" in names and "n199.md" in names and "n200.md" not in names and "c.md" not in names


def test_unit_prompts_join_every_column():
    cols = {"both": [{"session_id": "s", "slug": "r", "prompts": [1]}],
            "r1": [{"session_id": "s", "slug": "r", "prompts": [1, 4]}], "r2": [{"session_id": "s", "slug": "q", "prompts": [2]}]}
    assert tool.unit_prompts(cols) == {("s", "r"): [1, 4], ("s", "q"): [2]}


# --- outcomes and the bar ----------------------------------------------------------------

def _u(sid, slug, base, cur, sources=(), loaded=()):
    return {"session_id": sid, "slug": slug, "base": base, "cur": cur, "sources": list(sources),
            "loaded_notes": list(loaded)}


def test_both_raters_must_agree_and_a_missing_answer_is_pending():
    assert tool.both([{"A": 0, "B": True}, {"A": 0, "B": True}]) is True
    assert tool.both([{"A": 0, "B": True}, {"A": 0, "B": False}]) is False
    assert tool.both([{"A": 0, "B": True}, None]) is None


def test_rows_net_gains_against_losses_and_pending_counts_as_unchanged():
    units = [_u("s1", "a", False, True), _u("s1", "b", True, False), _u("s1", "c", True, True),
             _u("s2", "a", False, True), _u("s2", "d", False, None)]
    rows = tool.session_rows(units, ["s1", "s2", "s3"])
    assert [(r["units"], r["gained"], r["lost"], r["new"]) for r in rows] == [(3, 1, 1, 0), (2, 1, 0, 1), (0, 0, 0, 0)]
    assert [r["new"] for r in tool.session_rows(units, ["s1", "s2"], skip="a")] == [-1, 0]
    assert tool.top_rule(units) == "a"
    assert tool.carriers(units) == [("a", 2, 0), ("b", 0, 1)]


def test_a_gain_is_traced_to_a_note_that_was_or_was_not_loaded_or_to_nothing():
    assert tool.provenance_of(_u("s", "a", False, True, ["n.md"], [])) == "unloaded note"
    assert tool.provenance_of(_u("s", "a", False, True, ["n.md"], ["n.md"])) == "loaded note"
    assert tool.provenance_of(_u("s", "a", False, True)) == "absent"


def _heads(lines=100, chars=20000):
    return {"lines": {"p95": lines}, "chars": {"p95": chars}}


def test_estimate_and_the_bar_on_a_clear_gain():
    sessions = ["s%d" % i for i in range(30)]
    units = [_u(s, "r%d" % (i % 6), False, True) for i, s in enumerate(sessions)]
    e = tool.estimate(units, sessions, [1.0] * 20)
    assert (e["gained"], e["lost"], e["pending"]) == (30, 0, 0)
    assert abs(e["E"] - 1.0) < 1e-9 and e["without_top"]["rule"] == "r0"
    assert tool.decide(e, _heads()) == "build"
    assert tool.decide(e, _heads(lines=201)).startswith("do not build: (3)")


def test_the_bar_names_each_failed_condition():
    sessions = ["s%d" % i for i in range(30)]
    one_rule = [_u(s, "r", False, True) for s in sessions]
    e = tool.estimate(one_rule, sessions, [1.0] * 20)
    assert tool.decide(e, _heads()) == "do not build: (2) CI lower > 0 without the top rule"
    # a lift whose interval reaches 0 fails (1) whatever the coverage
    e = tool.estimate(one_rule, sessions, [0.0] * 19 + [1.0])
    assert tool.decide(e, _heads()).startswith("do not build: (1)")
    pending = tool.estimate([_u("s0", "r", False, None)], sessions, [1.0])
    assert tool.decide(pending, _heads()).startswith("pending")


def test_losses_pull_the_estimate_down():
    sessions = ["s%d" % i for i in range(10)]
    units = [_u(s, "a", False, True) for s in sessions] + [_u(s, "b", True, False) for s in sessions]
    e = tool.estimate(units, sessions, [1.0] * 5)
    assert e["E"] == 0.0 and (e["gained"], e["lost"]) == (10, 10)
