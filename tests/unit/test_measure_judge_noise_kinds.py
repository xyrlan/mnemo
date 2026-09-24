"""``tools/measure_judge_noise_kinds.py``: with the judge on, do generic rules still make the noise? (#484)

Over synthetic units only, no network. What these pin: a kind needs both
raters' letters, the struck-out counterfactual drops pairs and keeps the rest
labelled as they were, the bar is #479's, a request counts as all-G/N only
when every rule of its pool is, retiring keeps postings and only flags the
doc ``decide`` skips, the cached stage injects only what Jev scored at or
over the bar and records the rest as unknown, and the replay's self-check
compares injections prompt by prompt.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_judge_noise_kinds.py"
_spec = importlib.util.spec_from_file_location("measure_judge_noise_kinds", _TOOL)
mk = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mk
_spec.loader.exec_module(mk)


def _unit(uid, injected=(), scores=None, sid="s1", ts=1.0):
    u = {"uid": uid, "session_id": sid, "ts": ts, "pool": [{"slug": s} for s in injected]}
    if scores is not None:
        u["judge"] = {"asked": len(scores), "status": "ok", "scores": [list(x) for x in scores],
                      "fallback": False}
    return u


class _P:
    def __init__(self, sid, ts):
        self.session_id, self.ts = sid, ts


def test_a_kind_needs_both_letters_and_either_is_any_one():
    assert mk.kind(["G", "N"]) == {"rated": True, "both": True, "either": True}
    assert mk.kind(["G", "S"]) == {"rated": True, "both": False, "either": True}
    assert mk.kind(["G", None]) == {"rated": False, "both": False, "either": True}
    assert mk.kind(["S", "W"])["either"] is False


def test_rate_counts_labelled_pairs_only_and_reads_the_479_bar():
    pairs = [mk.nc.pair("u%d" % i, "r", label) for i, label in enumerate([0, 1, 2, 2, 2, None])]
    r = mk.rate(pairs)
    assert (r["pairs"], r["labelled"], r["noise"], r["on_point"], r["unlabelled"]) == (6, 5, 1, 3, 1)
    assert r["noise_share"] == 0.2 and r["clears"] is True, "the bar is <= 20%"
    lo, hi = r["ci"]
    assert lo < 0.2 < hi
    assert mk.rate([])["clears"] is None


def test_dropping_strikes_the_rules_pairs_and_leaves_the_rest():
    units = [_unit("u1", ["gen", "good"]), _unit("u2", ["gen"]), _unit("u3", ["good"])]
    labels = {"u1": {"gen": 0, "good": 2}, "u2": {"gen": 2}, "u3": {"good": 1}}
    pairs = mk.injected(units, labels)
    assert mk.rate(pairs)["noise"] == 1
    d = mk.dropped(pairs, {"gen", "never-injected"})
    assert d["rules_dropped"] == 1, "only rules that made a pair count as dropped"
    assert (d["labelled"], d["noise"], d["on_point"], d["marginal"]) == (2, 0, 1, 1)


def test_by_rule_carries_letters_and_sorts_by_noise_then_on_point():
    pairs = [mk.nc.pair("u1", "a", 2), mk.nc.pair("u2", "b", 0), mk.nc.pair("u3", "c", 2),
             mk.nc.pair("u4", "c", 2)]
    rows = mk.by_rule(pairs, {"b": ["G", "G"], "c": ["S", "S"]}, {"b": "feedback"})
    assert [r["slug"] for r in rows] == ["b", "c", "a"]
    assert rows[0]["kind_both"] and rows[0]["type"] == "feedback"
    assert rows[2]["letters"] == [None, None] and not rows[2]["kind_rated"]


def test_a_request_is_all_dropped_only_when_every_pool_rule_is():
    units = [_unit("u1", scores=[("g1", 0.1), ("g2", 0.2)]),
             _unit("u2", scores=[("g1", 0.1), ("s", 0.5)]),
             _unit("u3", scores=[("x", 0.1)]),
             _unit("u4")]  # nothing sent
    q = mk.requests(units, {"g1", "g2"}, known={"g1", "g2", "s"})
    assert q == {"requests": 3, "all_dropped": 1, "any_dropped": 2, "with_unrated": 1}


def test_retire_flags_the_doc_decide_skips_and_keeps_postings():
    from mnemo.core.reflex import decide

    index = {"docs": {"g": {"projects": ["p"]}, "s": {"projects": ["p"]}},
             "postings": {"t": [{"slug": "g"}, {"slug": "s"}]}, "doc_count": 2}
    out = mk.retire(index, {"g"})
    assert decide.candidates_for_project(out, "p") == ["s"]
    assert out["postings"] is index["postings"] and out["doc_count"] == 2
    assert not index["docs"]["g"].get("retired"), "the loaded index is not mutated"


def test_the_cached_stage_injects_what_jev_scored_and_logs_the_rest_unknown():
    units = [_unit("u1", scores=[("a", 0.7), ("b", 0.1)], sid="s", ts=5.0),
             _unit("u2", scores=[("a", 0.9)], sid="s", ts=6.0)]
    units[1]["judge"]["fallback"] = True
    log = {}
    stage = mk.cached_stage(mk.cached_scores(units), 0.4, log)
    assert stage(_P("s", 5.0), ["b", "a", "new"]) == ["a"]
    assert log[("s", 5.0)] == {"pool": ["b", "a", "new"], "unknown": ["new"]}
    assert stage(_P("s", 6.0), ["a"]) == [], "a fallback run left no scores to reuse"
    assert log[("s", 6.0)]["unknown"] == ["a"]


def test_replayed_reads_unknowns_requests_and_labels():
    recorded = [_unit("u1", ["a"], scores=[("a", 0.7)], ts=1.0),
                _unit("u2", ["g"], scores=[("g", 0.9)], ts=2.0)]
    mine = [dict(_unit("u1", ["a"], ts=1.0), stage={"pool": ["a"], "unknown": []}),
            dict(_unit("u2", ["s"], ts=2.0), stage={"pool": ["s", "t"], "unknown": ["s", "t"]})]
    labels = {"u1": {"a": 2}, "u2": {"t": 0}}
    r = mk.replayed(recorded, mine, labels)
    assert (r["labelled"], r["unlabelled"]) == (1, 1), "a pair #479 never labelled stays unlabelled"
    assert (r["requests"], r["requests_recorded"], r["requests_with_unknown"]) == (2, 2, 1)
    assert r["unknown"]["pairs"] == 2 and r["unknown"]["rules"] == 2
    assert r["unknown"]["labelled"] == {"0": 1}
    assert mk.reproduces(recorded, mine) == [1, 2]


def test_headroom_is_the_noise_the_bar_still_takes():
    r = mk.rate([mk.nc.pair("u%d" % i, "r", 0 if i == 0 else 2) for i in range(24)])
    assert mk.headroom(r) == 4, "(1+4)/(24+4) <= 20% < (1+5)/(24+5)"
    assert mk.headroom(mk.rate([mk.nc.pair("u", "r", 0)])) is None


def test_unknowns_by_rule_use_each_rules_own_pick_rate():
    units = [_unit("u%d" % i, scores=[("a", 0.9 if i < 1 else 0.1)], ts=float(i)) for i in range(4)]
    picks = mk.rule_picks(units, 0.4)
    assert picks == {"a": [4, 1]}
    mine = [{"stage": {"unknown": ["a", "z"]}}, {"stage": {"unknown": ["a"]}}, {"stage": None}]
    rows = mk.unknown_by_rule(mine, picks)
    assert rows[0] == {"slug": "a", "pairs": 2, "scored": 4, "picked": 1, "expected": 0.5}
    assert rows[1]["slug"] == "z" and rows[1]["expected"] is None
    assert mk.pick_rate(units, {"a"}, 0.4) == [0, 0]


def test_only_views_480_did_not_rate_by_both_are_rated_once_each():
    texts = {"b/x": "same view", "c/x": "same view", "b/y": "rated", "b/z": "half"}
    owners = {k: [k.split("/")] for k in texts}
    have = {mk.nc.text_id("rated"): ["G", "G"], mk.nc.text_id("half"): ["G", None]}
    rows = mk.rated_rows(texts, have, owners)
    assert sorted(r["text"] for r in rows) == ["half", "same view"]
    same = next(r for r in rows if r["text"] == "same view")
    assert sorted(same["slugs"]) == [["b", "x"], ["c", "x"]]


def test_letters_come_from_480_first_then_this_tool():
    theirs = {"c1": {"id1": "G"}, "c2": {}}
    mine = {"c1": {"id1": "S"}, "c2": {"id1": "N"}}
    assert mk.letters_for("id1", [theirs, mine], ["c1", "c2"]) == ["G", "N"]
    assert mk.letters_for("nope", [theirs, mine], ["c1", "c2"]) == [None, None]


def test_build_and_report_over_a_synthetic_arm():
    units = [_unit("u1", ["g"], scores=[("g", 0.8)], ts=1.0), _unit("u2", ["s"], scores=[("s", 0.6)], ts=2.0)]
    arms = {"b": {"units": units, "injected": {"g", "s"}, "pool": {"g", "s"},
                  "facts": {"g": {"type": "feedback"}, "s": {"type": "reference"}}}}
    letters = {"b": {"g": ["G", "G"], "s": ["S", "S"]}}
    labels = {"u1": {"g": 0}, "u2": {"s": 2}}
    replays = {"b": {"none": [dict(u, stage=None) for u in units],
                     "both": [dict(_unit("u1", [], ts=1.0), stage=None),
                              dict(_unit("u2", ["s"], ts=2.0), stage={"pool": ["s"], "unknown": []})],
                     "either": [dict(_unit("u1", [], ts=1.0), stage=None),
                                dict(_unit("u2", ["s"], ts=2.0), stage={"pool": ["s"], "unknown": []})]}}
    data = mk.build(arms, letters, labels, replays)
    b = data["arms"]["b"]
    assert b["on"]["noise_share"] == 0.5 and b["dropped"]["both"]["noise_share"] == 0.0
    assert b["requests"]["both"]["all_dropped"] == 1
    assert b["replay_check"] == [2, 2]
    assert b["replayed"]["both"]["requests"] == 1 and b["replayed"]["both"]["requests_recorded"] == 2
    text = "\n".join(mk.report_lines(data))
    assert "arm (b)" in text and "clears" in text and "misses" in text
