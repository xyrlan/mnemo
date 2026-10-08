"""``tools/measure_relevance_block.py`` over hand-built caches and transcripts
whose answers are known by construction (#613)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_relevance_block as tool  # noqa: E402

DOCS = {"a": {"projects": ["app"]}, "b": {"projects": ["app"]}, "c": {"universal": True},
        "d": {"projects": ["other"]}, "late": {"projects": ["app"]}}
DATES = {s: {"stamped": 10.0, "has_row": True, "sources": []} for s in DOCS}
DATES["late"] = {"stamped": 100.0, "has_row": True, "sources": []}
META = {"s1": {"project": "app", "start": 20.0}, "s2": {"project": "app", "start": 40.0},
        "s3": {"project": "app", "start": 60.0}, "s4": {"project": "app", "start": 200.0}}


def _units(*pairs, redundant=()):
    return [{"session_id": sid, "slug": slug, "strict": False, "redundant": (sid, slug) in redundant}
            for sid, slug in pairs]


# --- 1. source (a): rater labels, as of each session's start -----------------------------

def test_rater_events_date_each_unit_at_its_session_start():
    units = _units(("s1", "a"), ("s2", "a"), ("s2", "b"), ("gone", "a"))
    assert tool.rater_events(units, META) == {"a": [("s1", 20.0), ("s2", 40.0)], "b": [("s2", 40.0)]}
    assert tool.rater_events(units, META, since=30.0) == {"a": [("s2", 40.0)], "b": [("s2", 40.0)]}


def test_block_ranks_by_earlier_sessions_only_and_never_the_session_itself():
    units = _units(("s1", "a"), ("s2", "a"), ("s2", "b"), ("s3", "b"), ("s3", "c"))
    ev = tool.rater_events(units, META)
    blocks = tool.blocks_for(["s1", "s2", "s3", "s4"], META, ev, DOCS, DATES, 15, {})
    assert blocks["s1"] == []                      # nothing before it, and its own unit never counts
    assert blocks["s2"] == ["a"]                   # s1 only; s2's own b does not count
    assert blocks["s3"] == ["a", "b"]              # a: s1, s2 (2); b: s2 (1); s3's c does not count
    assert blocks["s4"] == ["a", "b", "c"]         # a 2, b 2, c 1; ties by slug
    assert tool.blocks_for(["s4"], META, ev, DOCS, DATES, 1, {})["s4"] == ["a"]


def test_block_drops_rules_not_yet_learned_other_projects_and_redundant():
    units = _units(("s1", "late"), ("s1", "d"), ("s1", "a"), ("s2", "a"))
    ev = tool.rater_events(units, META)
    assert tool.blocks_for(["s3"], META, ev, DOCS, DATES, 15, {})["s3"] == ["a"]
    assert tool.blocks_for(["s4"], META, ev, DOCS, DATES, 15, {})["s4"] == ["a", "late"]
    assert tool.blocks_for(["s4"], META, ev, DOCS, DATES, 15, {"s4": {"a"}})["s4"] == ["late"]


# --- 1. source (b): the judge's picks -----------------------------------------------------

def _row(sid, ts, scores, status="ok"):
    return {"session_id": sid, "ts": ts, "judge": {"status": status, "scores": scores}}


def test_log_picks_take_scores_at_or_above_inject_at():
    rows = [
        _row("x", "2026-10-01T00:00:00Z", [["a", 0.4], ["b", 0.39], ["c", 0.9]]),
        _row("y", "2026-10-01T00:00:00Z", [["a", 0.9]], status="timeout"),
        {"session_id": "z", "ts": "2026-10-01T00:00:00Z", "emitted": ["a"]},  # no judge
        "junk",
    ]
    got = tool.log_picks(rows, 0.4)
    assert sorted(s for s, _, _ in got) == ["a", "c"]
    assert {sid for _, sid, _ in got} == {"x"}


def test_transcript_picks_read_reflex_heads_after_since():
    def att(ts, text, side=False):
        return json.dumps({"type": "attachment", "timestamp": ts, "isSidechain": side,
                           "attachment": {"type": "hook_additional_context", "hookName": "UserPromptSubmit",
                                          "content": [text]}})
    full = "mnemo reflex context:\n• [[a]]:\nbody naming [[other]]\n• [[b]]:\nbody"
    lines = [att("2026-09-19T00:00:00Z", full),                     # before since
             att("2026-09-21T00:00:00Z", full),
             att("2026-09-22T00:00:00Z", full, side=True),          # sidechain
             att("2026-09-23T00:00:00Z", "mnemo://v1 project=app"),  # not a reflex block
             "not json mnemo reflex context"]
    since = tool.mrc.epoch("2026-09-20T00:00:00Z")
    got = tool.transcript_picks(lines, "s", since)
    assert [(slug, sid) for slug, sid, _ in got] == [("a", "s"), ("b", "s")]


def test_pick_events_one_entry_per_session_at_the_first_pick():
    got = tool.pick_events([("a", "x", 5.0), ("a", "x", 3.0), ("a", "y", 9.0), ("b", "x", 1.0)])
    assert got == {"a": [("x", 3.0), ("y", 9.0)], "b": [("x", 1.0)]}


def test_picks_count_only_before_the_session_start():
    ev = tool.pick_events([("a", "x", 30.0), ("b", "y", 50.0), ("b", "z", 55.0), ("c", "s3", 58.0)])
    blocks = tool.blocks_for(["s2", "s3", "s4"], META, ev, DOCS, DATES, 15, {})
    # s2 (t=40) sees only a's pick; s3 never counts its own pick of c
    assert blocks == {"s2": ["a"], "s3": ["b", "a"], "s4": ["b", "a", "c"]}


def test_window_and_with_history():
    ev = {"a": [("x", 30.0), ("y", 50.0)], "b": [("z", None)]}
    assert tool.window(ev) == (30.0, 50.0)
    assert tool.window({}) is None
    assert tool.with_history(["s1", "s2", "s3"], META, ev) == 2
    assert tool.with_history(["s1"], META, {}) == 0


# --- 1. coverage, the estimate and the bar --------------------------------------------------

def _rows_units(n_sessions, covered):
    """``n_sessions`` sessions with one broad unit of rule ``r<i>``; ``covered``
    of them have it in the block."""
    units = _units(*[("t%d" % i, "r%d" % i) for i in range(n_sessions)])
    per = {"t%d" % i: (["r%d" % i] if i < covered else []) for i in range(n_sessions)}
    return units, ["t%d" % i for i in range(n_sessions)], per


def test_block_numbers_coverage_and_estimate_per_lift():
    units, sessions, per = _rows_units(10, 4)
    got = tool.block_numbers(units, sessions, per, {"beside": [0.5] * 20, "distance": [0.25] * 20}, {})
    assert (got["coverage"][tool.BROAD]["k"], got["coverage"][tool.BROAD]["n"]) == (4, 10)
    assert abs(got["estimate"][tool.BROAD]["beside"]["E"] - 0.2) < 1e-9
    assert abs(got["estimate"][tool.BROAD]["distance"]["E"] - 0.1) < 1e-9
    w = got["estimate"][tool.BROAD]["distance"]["without_top"]
    assert (w["rule"], w["carried"]) == ("r0", 1)
    assert abs(w["E"] - 0.075) < 1e-9


def test_without_top_drops_the_rule_that_carried_most():
    units = _units(*[("t%d" % i, "big") for i in range(6)] + [("t0", "small")])
    sessions = ["t%d" % i for i in range(6)]
    per = {s: ["big", "small"] for s in sessions}
    got = tool.block_numbers(units, sessions, per, {"distance": [1.0] * 10}, {})
    w = got["estimate"][tool.BROAD]["distance"]["without_top"]
    assert (w["rule"], w["carried"]) == ("big", 6)
    assert abs(w["E"] - 1 / 6) < 1e-9


def _stats(e, lo, w_e=None, w_lo=None):
    s = {"E": e, "ci": {"E": [lo, e * 2]}}
    if w_e is not None:
        s["without_top"] = {"E": w_e, "ci": {"E": [w_lo, w_e * 2]}}
    return s


def _results(by_k):
    return {k: {"estimate": {tool.BROAD: {"distance": v}}} for k, v in by_k.items()}


def test_passes_needs_positive_with_and_without_top():
    assert tool.passes(_stats(0.2, 0.1, 0.1, 0.01))
    assert not tool.passes(_stats(0.2, 0.1, 0.05, 0.01))   # without top under 1/15
    assert not tool.passes(_stats(0.2, 0.1, 0.1, 0.0))     # without top CI touches 0
    assert not tool.passes(_stats(0.2, 0.0, 0.1, 0.01))    # CI lower not over 0
    assert not tool.passes(_stats(0.05, 0.01, 0.1, 0.01))  # under 1/15
    assert not tool.passes(_stats(0.2, 0.1))               # no top rule to drop


def test_best_k_is_highest_estimate_ties_to_smaller_k():
    r = _results({"5": _stats(0.1, 0), "10": _stats(0.3, 0), "25": _stats(0.3, 0)})
    assert tool.best_k(r, "distance") == "10"
    assert tool.best_k({}, "distance") is None


def test_decide_follows_the_declared_bar():
    good, bad = _stats(0.2, 0.1, 0.1, 0.01), _stats(0.2, 0.1, 0.01, 0.0)
    assert tool.decide({"a": _results({"5": bad}), "b": _results({"5": good})}) == "build"
    assert tool.decide({"a": _results({"5": good}), "b": _results({"5": bad})}).startswith("only source (a)")
    assert tool.decide({"a": _results({"5": bad}), "b": _results({"5": bad})}) == "do not build"
    # the best K of (b) decides, and it must pass on its own
    assert tool.decide({"a": {}, "b": _results({"5": good, "10": _stats(0.5, 0.1, 0.01, 0.0)})}) == "do not build"
    assert tool.decide({"a": {}, "b": {}}).startswith("pending")


# --- 2. lift at a distance --------------------------------------------------------------------

def _t(kind, ts, content, **kw):
    return json.dumps(dict({"type": kind, "timestamp": ts, "message": {"role": kind, "content": content}}, **kw))


def _uid(sid, ts, text):
    return "%s|%s" % (sid, text)


TRANSCRIPT = [
    _t("user", "2026-09-01T00:00:00Z", "first ask"),
    _t("assistant", "2026-09-01T00:00:01Z", [{"type": "text", "text": "first reply"}]),
    _t("assistant", "2026-09-01T00:00:02Z", [{"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]),
    _t("assistant", "2026-09-01T00:00:03Z", [{"type": "text", "text": "more"}]),
    _t("user", "2026-09-01T00:00:04Z", "second ask"),
    _t("assistant", "2026-09-01T00:00:05Z", [{"type": "text", "text": "side"}], isSidechain=True),
    _t("assistant", "2026-09-01T00:00:06Z", [{"type": "text", "text": "second reply"}]),
    _t("user", "2026-09-01T00:00:07Z", "the prompt"),
    _t("assistant", "2026-09-01T00:00:08Z", [{"type": "text", "text": "after"}]),
]


def test_session_turns_are_everything_before_the_prompt():
    turns, prompt = tool.session_turns(TRANSCRIPT, "s", "s|the prompt", _uid)
    assert prompt == "the prompt"
    assert turns == [("user", "first ask"), ("assistant", "first reply\n\nmore"),
                     ("user", "second ask"), ("assistant", "second reply")]
    assert tool.session_turns(TRANSCRIPT, "s", "s|first ask", _uid) == ([], "first ask")
    assert tool.session_turns(TRANSCRIPT, "s", "s|missing", _uid) is None


def test_history_keeps_the_last_whole_turns_within_the_limit():
    turns = [("user", "a" * 10), ("assistant", "b" * 10), ("user", "c" * 10)]
    assert tool.history(turns, 100) == (turns, 0)
    assert tool.history(turns, 20) == ([("assistant", "b" * 10), ("user", "c" * 10)], 1)
    kept, dropped = tool.history(turns, 15)
    assert kept == [("assistant", "…" + "b" * 5), ("user", "c" * 10)] and dropped == 1


PAIR = {"id": "u:a", "uid": "u", "slug": "a", "project": "app", "rule": "Do X.", "prompt": "stored",
        "previous": "prev reply"}


def test_distance_pair_and_arms_differ_only_by_the_block():
    p = tool.distance_pair(PAIR, ([("user", "q"), ("assistant", "r")], "the prompt"))
    assert (p["turns_before"], p["chars_before"], p["context"]) == (2, 2, True)
    a, b = tool.arm_prompt(p, "A"), tool.arm_prompt(p, "B")
    assert b.startswith("<system-reminder>\nSessionStart hook additional context: mnemo settled rules:\n"
                        "• [[a]]:\nDo X.\n</system-reminder>\n\n")
    assert b.endswith(a) and "Do X." not in a
    assert a.index("<user>\nq\n</user>") < a.index("<assistant>\nr\n</assistant>") < a.index("the prompt")
    lost = tool.distance_pair(PAIR, None)
    assert (lost["context"], lost["turns"], lost["prompt"]) == (False, [["assistant", "prev reply"]], "stored")


def test_mask_hides_the_block_header_and_slug():
    assert tool.mask("Per mnemo settled rules, [[x]] says rule-a", "rule-a") == "Per [...], [...] says [...]"


def test_lift_diffs_pair_b_minus_a_and_drop_both_na():
    pairs = [{"id": "p1", "slug": "a", "rule": "r"}, {"id": "p2", "slug": "b", "rule": "r"},
             {"id": "p3", "slug": "c", "rule": "r"}]
    answers = {p["id"]: {"A": [{"text": "x"}], "B": [{"text": "y"}]} for p in pairs}
    aid = tool.rl.answer_id
    verdicts = {aid("p1", "A", 0): "no", aid("p1", "B", 0): "yes",
                aid("p2", "A", 0): "yes", aid("p2", "B", 0): "yes",
                aid("p3", "A", 0): "na", aid("p3", "B", 0): "na"}
    diffs, stats = tool.lift_diffs(pairs, answers, verdicts)
    assert sorted(diffs) == [0.0, 1.0]
    assert (stats["n"], stats["excluded_na"], stats["lift"]) == (2, 1, 0.5)


def test_judge_items_are_masked_and_blind_to_the_arm():
    pairs = [{"id": "p1", "slug": "rule-a", "rule": "r"}]
    answers = {"p1": {"A": [{"text": "plain"}], "B": [{"text": "mnemo settled rules: [[rule-a]]"}]}}
    items = tool.judge_items(pairs, answers, {})
    assert sorted(i["answer"] for i in items) == ["[...]: [...]", "plain"]
    assert all(set(i) == {"id", "rule", "answer"} for i in items)


def test_notional_counts_pending_calls():
    p = tool.distance_pair(PAIR, ([], "x"))
    e = tool.notional([p], 4, 2)
    assert (e["arm_calls"], e["judge_calls"]) == (4, 1) and e["usd"] > 0


def test_block_numbers_read_only_the_sessions_given():
    # t0..t3 carry rule "big" in the block; only t4, t5 are in scope, carrying "small"
    units = _units(*[("t%d" % i, "big") for i in range(4)] + [("t4", "small"), ("t5", "small")])
    per = {"t%d" % i: ["big"] for i in range(4)}
    per.update({"t4": ["small"], "t5": ["small"]})
    got = tool.block_numbers(units, ["t4", "t5"], per, {"distance": [1.0] * 10}, {})
    assert (got["coverage"][tool.BROAD]["k"], got["coverage"][tool.BROAD]["n"]) == (2, 2)
    w = got["estimate"][tool.BROAD]["distance"]["without_top"]
    assert (w["rule"], w["carried"]) == ("small", 2)


def test_block_numbers_count_blocks_over_the_envelope():
    units = _units(("t0", "a"), ("t1", "b"))
    per = {"t0": ["a"], "t1": ["a", "b"], "t2": []}
    bodies = {"a": "x" * 50, "b": "y" * 50}
    one = tool.sb.block_bytes(["a"], bodies)
    got = tool.block_numbers(units, ["t0", "t1", "t2"], per, {"beside": [1.0]}, bodies, envelope=one)
    assert got["bytes"]["over_envelope"] == 1
    assert tool.block_numbers(units, ["t0"], per, {"beside": [1.0]}, bodies)["bytes"]["over_envelope"] is None


# --- #618: a block that fits ------------------------------------------------------------------

def _rule(slug, body, quote=None, universal=False, name=None):
    from mnemo.core.export.select import ExportRule

    return ExportRule(slug=slug, name=name or slug, body=body, quote=quote, universal=universal,
                      source_count=0, page_type="feedback")


RULES = {"a": _rule("a", "Do X first. Then Y.\n\n**Why:** long reason.\n", quote="do x", name="Do X"),
         "b": _rule("b", "Never Z.\n\n**How to apply:** always.\n", universal=True)}


def test_export_rules_read_lead_quote_and_universal_from_the_vault(tmp_path):
    page = tmp_path / "shared" / "feedback" / "a.md"
    page.parent.mkdir(parents=True)
    page.write_text('---\nslug: a\nname: Do X\nevidence:\n  quote: "do x"\n---\nDo X first.\n\n**Why:** r.\n',
                    encoding="utf-8")
    inbox = tmp_path / "shared" / "_inbox" / "feedback" / "b.md"
    inbox.parent.mkdir(parents=True)
    inbox.write_text("---\nname: B\n---\nDraft.\n", encoding="utf-8")
    got = tool.export_rules(tmp_path, {"a": {"universal": True}})
    assert set(got) == {"a"}
    r = got["a"]
    assert (r.name, r.quote, r.universal) == ("Do X", "do x", True)
    assert r.body.startswith("Do X first.")


def test_first_sentence_is_the_lead_paragraphs_first_on_one_line():
    assert tool.first_sentence("Do X first. Then Y.\n\nMore.") == "Do X first."
    assert tool.first_sentence("One line\nwrapped here, e.g. like this. Next.") == "One line wrapped here, e.g. like this."
    assert tool.first_sentence("No stop at all") == "No stop at all"


def test_entry_text_in_each_form():
    from mnemo.core.export.render import render_entry

    assert tool.entry_text("a", "compact", RULES, {}) == render_entry(RULES["a"], full=False).rstrip("\n")
    assert tool.entry_text("a", "compact", RULES, {}) == '### Do X  `a`\nDo X first. Then Y.\n> you said: "do x"'
    assert tool.entry_text("b", "line", RULES, {}) == "• [[b]]: Never Z."
    assert tool.entry_text("a", "full", RULES, {"a": "whole body"}) == "• [[a]]:\nwhole body"
    assert tool.entry_text("gone", "compact", RULES, {}) == "• [[gone]]"


def test_form_block_and_bytes():
    assert tool.form_block([], "compact", RULES, {}) == ""
    assert tool.form_bytes([], "line", RULES, {}) == 0
    line = tool.form_block(["a", "b"], "line", RULES, {})
    assert line == "mnemo settled rules:\n• [[a]]: Do X first.\n• [[b]]: Never Z."
    assert tool.form_bytes(["a", "b"], "line", RULES, {}) == len(line.encode("utf-8"))
    compact = tool.form_block(["a", "b"], "compact", RULES, {})
    assert compact.count("\n\n") == 1 and "**Why:**" not in compact
    # the full form is #616's block, byte for byte
    assert tool.form_bytes(["a"], "full", RULES, {"a": "x" * 40}) == tool.sb.block_bytes(["a"], {"a": "x" * 40})


def test_block_numbers_bytes_by_form():
    units = _units(("t0", "a"))
    got = tool.block_numbers(units, ["t0", "t1"], {"t0": ["a", "b"], "t1": ["a"]}, {"beside": [1.0]},
                             {"a": "x" * 500, "b": "y"}, rules=RULES)
    by = got["bytes_by_form"]
    assert set(by) == set(tool.FORMS)
    assert by["line"]["max"] == tool.form_bytes(["a", "b"], "line", RULES, {})
    assert by["full"]["max"] == got["bytes"]["max"]
    assert "bytes_by_form" not in tool.block_numbers(units, ["t0"], {"t0": ["a"]}, {"beside": [1.0]}, {})


def _inject(ts, n, tool_name="session_start.inject"):
    return {"tool": tool_name, "timestamp": ts, "envelope_bytes": n}


def test_envelope_room_reads_inject_rows_since_and_leaves_the_rest():
    since = tool.mrc.epoch("2026-09-29T11:04:00Z")
    rows = [_inject("2026-09-29T11:00:00Z", 8000),           # before the index went live
            _inject("2026-10-01T00:00:00Z", 8000, "read"),   # another tool
            "junk", {"tool": "session_start.inject", "timestamp": "2026-10-01T00:00:00Z"}]
    rows += [_inject("2026-10-01T00:00:%02dZ" % i, 1000 * (i + 1)) for i in range(5)]
    got = tool.envelope_room(rows, since, 9000)
    assert (got["n"], got["median"], got["p95"], got["max"]) == (5, 3000, 5000, 5000)
    assert (got["room_median"], got["room_p95"]) == (6000, 4000)
    assert tool.envelope_room([], since, 9000)["room_p95"] is None


def test_fits_reads_p95_against_the_budget():
    assert tool.fits({"p95": 3000}, 3000)
    assert not tool.fits({"p95": 3001}, 3000)
    assert not tool.fits({"p95": 0}, 3000)       # an empty block fits nothing
    assert not tool.fits({"p95": 10}, None)
    assert not tool.fits({"p95": None}, 3000)


def _ctx(parent, texts, event="SessionStart"):
    return json.dumps({"parentUuid": parent, "attachment": {
        "type": "hook_additional_context", "hookEvent": event, "content": texts}})


def test_original_chars_reads_the_size_a_persisted_preview_names():
    assert tool.original_chars("x" * 12) == 12
    assert tool.original_chars("<persisted-output>\nOutput too large (10.4KB). Full ...") == int(10.4 * 1024)
    assert tool.original_chars("<persisted-output>\nOutput too large (900B). ...") == 900


def test_hook_split_counts_summed_firings_and_persisted_ones():
    big = "<persisted-output>\nOutput too large (11KB). Full output saved\nPreview..."
    lines = [_ctx("p1", ["x" * 6000]), _ctx("p1", ["y" * 6000]),         # summed 12k, each under: inline
             _ctx("p2", ["x" * 5600, big]),                               # one was over alone: not evidence
             _ctx("p3", ["x" * 100, "y" * 100]),                          # under together
             _ctx("p4", ["x" * 6000]),                                    # one context only
             _ctx("p5", ["x" * 6000, "<persisted-output>\nOutput too large (6KB). x"]),  # summed AND persisted
             _ctx("p6", ["x" * 9000, "y" * 9000], event="UserPromptSubmit"),
             "not json hook_additional_context"]
    assert tool.hook_split(lines) == {"firings": 4, "summed_over_each_under": 2, "persisted": 1}


DOCS2 = {"t": {"projects": ["app"]}, "m1": {"projects": ["app"]}, "m2": {"universal": True},
         "m3": {"projects": ["app"]}, "other": {"projects": ["zzz"]}, "late": {"projects": ["app"]},
         "old": {"projects": ["app"], "retired": True}}
DATES2 = {s: {"stamped": 10.0, "has_row": True, "sources": []} for s in DOCS2}
DATES2["late"] = {"stamped": 500.0, "has_row": True, "sources": []}


def test_mates_rank_bs_log_among_rules_that_existed_never_the_target_or_its_session():
    ev = {"t": [("x1", 1.0), ("x2", 2.0), ("x3", 3.0)],
          "m1": [("x1", 1.0), ("x2", 2.0)], "m2": [("x1", 1.0), ("x2", 2.0)],
          "m3": [("own", 1.0), ("x1", 1.0), ("x9", 1.0)], "other": [("x1", 1.0)] * 5,
          "late": [("x1", 1.0), ("x2", 2.0), ("x3", 3.0)], "old": [("x1", 1.0)]}
    assert tool.mates("t", "app", 100.0, ev, DOCS2, DATES2, 10, "own") == ["m1", "m2", "m3"]
    assert tool.mates("t", "app", 100.0, ev, DOCS2, DATES2, 2, "own") == ["m1", "m2"]
    # "own" no longer the session: it counts, and m3's 3 sessions put it first
    assert tool.mates("t", "app", 100.0, ev, DOCS2, DATES2, 10, "") == ["m3", "m1", "m2"]
    assert tool.mates("t", "app", 600.0, ev, DOCS2, DATES2, 1, "own") == ["late"]
    assert tool.mates("m1", "app", 100.0, ev, DOCS2, DATES2, 10, "own")[0] == "t"


def test_diluted_pair_puts_the_target_among_k_minus_1_and_leaves_arm_a_alone():
    base = tool.distance_pair(PAIR, ([("user", "q")], "the prompt"))
    rules = {"m%d" % i: _rule("m%d" % i, "Mate %d says." % i) for i in range(6)}
    rules["a"] = _rule("a", "Do X.")
    got = tool.diluted_pair(base, ["a"] + ["m%d" % i for i in range(6)], 4, rules, {}, "line")
    assert got["mates"] == ["m0", "m1", "m2"] and got["k"] == 4
    lines = got["block"].splitlines()
    assert lines[0] == "mnemo settled rules:" and len(lines) == 5
    assert lines[1 + got["position"]] == "• [[a]]: Do X."
    assert tool.arm_prompt(got, "A") == tool.arm_prompt(base, "A")
    assert tool.arm_prompt(got, "B") != tool.arm_prompt(base, "B")
    # the position is seeded by the pair: the same every run, and not always the same slot
    again = tool.diluted_pair(base, ["m%d" % i for i in range(6)], 4, rules, {}, "line")
    assert again["position"] == got["position"]
    spots = {tool.diluted_pair(dict(base, id="p%d" % i), ["m%d" % i for i in range(6)], 7, rules, {}, "line")["position"]
             for i in range(30)}
    assert len(spots) > 2
    short = tool.diluted_pair(base, ["m0"], 15, rules, {}, "compact")
    assert short["k"] == 2 and short["block"].count("### ") == 2


def test_seed_arm_a_copies_only_arm_a_and_its_verdicts_without_overwriting():
    aid = tool.rl.answer_id
    pairs = [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}]
    old = {"p1": {"A": [{"text": "a0"}, {"text": "a1"}], "B": [{"text": "b0"}]},
           "p2": {"A": [{"text": "old"}]}}
    old_v = {aid("p1", "A", 0): "yes", aid("p1", "A", 1): "no", aid("p1", "B", 0): "yes", aid("p2", "A", 0): "na"}
    answers = {"p2": {"A": [{"text": "mine"}]}}
    verdicts = {}
    assert tool.seed_arm_a(pairs, answers, verdicts, old, old_v) == 2
    assert [a["text"] for a in answers["p1"]["A"]] == ["a0", "a1"] and "B" not in answers["p1"]
    assert answers["p2"]["A"] == [{"text": "mine"}]
    assert verdicts == {aid("p1", "A", 0): "yes", aid("p1", "A", 1): "no"}
    assert tool.seed_arm_a(pairs, answers, verdicts, old, old_v) == 0


def test_diluted_judge_items_mask_every_mate():
    pairs = [{"id": "p1", "slug": "rule-a", "rule": "r", "mates": ["mate-b", "mate-c"]}]
    answers = {"p1": {"A": [{"text": "plain"}], "B": [{"text": "per Mate-B and mate-c, rule-a"}]}}
    items = tool.diluted_judge_items(pairs, answers, {})
    assert sorted(i["answer"] for i in items) == ["per [...] and [...], [...]", "plain"]


def _fit_k(by_form, **lifts):
    """One K's results: p95 bytes per form, and the broad estimate per lift."""
    return {"bytes_by_form": {f: {"p95": n} for f, n in by_form.items()},
            "estimate": {tool.BROAD: lifts}}


GOOD, BAD = _stats(0.2, 0.1, 0.1, 0.01), _stats(0.2, 0.1, 0.01, 0.0)
BUDGETS = {"a": 3000, "b": 9000}


def test_fitting_lists_the_ks_whose_p95_fits():
    by_k = {"5": _fit_k({"compact": 2000, "line": 800}), "15": _fit_k({"compact": 8000, "line": 2900}),
            "25": _fit_k({"compact": 11000, "line": 4600})}
    assert tool.fitting(by_k, "compact", 9000) == ["5", "15"]
    assert tool.fitting(by_k, "line", 3000) == ["5", "15"]
    assert tool.fitting(by_k, "compact", None) == []


def test_decide_fit_builds_on_a_fitting_k_in_that_budgets_form():
    by_k = {"5": _fit_k({"compact": 2000, "line": 800}, **{"diluted-compact": BAD, "diluted-line": BAD}),
            "15": _fit_k({"compact": 8000, "line": 2900}, **{"diluted-compact": GOOD, "diluted-line": BAD})}
    assert tool.decide_fit(by_k, BUDGETS) == ("build", "15", "b")
    by_k["5"]["estimate"][tool.BROAD]["diluted-line"] = GOOD
    assert tool.decide_fit(by_k, BUDGETS) == ("build", "5", "a")   # budget (a) is tried first, smallest K first
    # a lift in one form never decides a block in the other
    only_line = {"15": _fit_k({"compact": 9500, "line": 2900}, **{"diluted-compact": GOOD, "diluted-line": BAD})}
    assert tool.decide_fit(only_line, BUDGETS)[0] != "build"


def test_decide_fit_names_the_constraint_that_failed():
    none_fit = {"25": _fit_k({"compact": 11000, "line": 4600}, **{"diluted-compact": GOOD, "diluted-line": GOOD})}
    assert tool.decide_fit(none_fit, BUDGETS)[0] == "do not build: bytes (no K fits a budget)"
    unfit = {"5": _fit_k({"compact": 2000, "line": 800}, **{"diluted-compact": BAD, "diluted-line": BAD}),
             "25": _fit_k({"compact": 11000, "line": 4600}, **{"diluted-compact": GOOD, "diluted-line": BAD})}
    assert tool.decide_fit(unfit, BUDGETS)[0].startswith("do not build: bytes (only")
    diluted = {"15": _fit_k({"compact": 8000, "line": 2900},
                            **{"diluted-compact": BAD, "diluted-line": BAD, "distance": GOOD})}
    assert tool.decide_fit(diluted, BUDGETS)[0].startswith("do not build: dilution")
    coverage = {"15": _fit_k({"compact": 8000, "line": 2900},
                             **{"diluted-compact": BAD, "diluted-line": BAD, "distance": BAD})}
    assert tool.decide_fit(coverage, BUDGETS)[0].startswith("do not build: coverage")
