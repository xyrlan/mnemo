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


# --- (b) from mnemo's own judge-picks ledger (#619) -------------------------------------

def _ledger_with(vault, picks_list):
    from mnemo.core.reflex import picks
    for slug, sid, ts in picks_list:
        picks.record(vault, session_id=sid, project="app", picks=[slug], ts=ts)
    return picks.load(vault)


def test_the_ledger_ranks_every_block_as_the_rebuilt_history_does(tmp_path):
    """The same picks, read through ``picks_before`` or rebuilt as events,
    fill the same block for every session at every K."""
    raw = [("a", "x", 30.0), ("a", "x", 35.0), ("b", "y", 50.0), ("b", "z", 55.0), ("c", "s3", 58.0),
           ("a", "s3", 59.0), ("late", "y", 150.0), ("d", "y", 51.0), ("c", "w", 199.0)]
    ledger = _ledger_with(tmp_path, raw)
    ev = tool.pick_events(raw)
    sessions = ["s1", "s2", "s3", "s4"]
    for k in (1, 2, 15):
        for redundant in ({}, {"s4": {"a"}}):
            assert tool.blocks_for(sessions, META, ledger, DOCS, DATES, k, redundant) == \
                tool.blocks_for(sessions, META, ev, DOCS, DATES, k, redundant)
    assert tool.blocks_for(["s4"], META, ledger, DOCS, DATES, 15, {})["s4"] == ["a", "b", "c", "late"]
    assert tool.window(ledger) == tool.window(ev) == (30.0, 199.0)
    assert tool.with_history(sessions, META, ledger) == tool.with_history(sessions, META, ev) == 3


def test_source_b_reads_the_ledger_only_when_it_covers_the_judges_life(tmp_path):
    from mnemo.core.reflex import picks
    vault, projects = tmp_path / "vault", tmp_path / "projects"
    projects.mkdir()
    live = tool.mrc.epoch(tool.JUDGE_LIVE)
    _ledger_with(vault, [("a", "x", live + 3600)])
    got, counts = tool._source_b(vault, projects, tool.INJECT_AT)
    assert counts["source"] == "rebuilt" and got == {}      # the hook's rows alone start too late
    picks.backfill(vault, [{"ts": "2026-09-20T18:56:10Z", "session_id": "old", "project": "app",
                            "picks": ["b"]}], covers_since=live)
    got, counts = tool._source_b(vault, projects, tool.INJECT_AT)
    assert counts["source"] == "ledger" and counts["log_since"] == live
    assert got.picks_before(None, float("inf")) == {"a": 1, "b": 1}
