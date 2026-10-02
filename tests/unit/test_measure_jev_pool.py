"""``tools/measure_jev_pool.py`` over synthetic prompts and scores.

No model and no network. What is pinned is what the measurement has to be
right about: the split is stable, a pool of K asks only the first K rules and
picks with the shipped bar, the replay is the hook's dedupe and cap, a unit is
reached only by an injection at or before a prompt it applies to (or by the
SessionStart/MCP delivery #520 recorded), unlabelled pairs are kept apart from
labelled ones, the dev choice follows its pre-registered rule, the verdict
names every failed condition, and an interrupted send resumes.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_jev_pool.py"
_spec = importlib.util.spec_from_file_location("measure_jev_pool", _TOOL)
mjp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mjp)


def _prompt(sid, i, ranking, tokens_ok=True):
    return {"key": "%s#%d" % (sid, i), "session_id": sid, "i": i, "ts": float(i), "project": "p",
            "text": "prompt %d" % i, "tokens_ok": tokens_ok, "ranking": list(ranking)}


def _ok(scores):
    return {"status": "ok", "ms": 400, "scores": dict(scores)}


# --- split and labels ----------------------------------------------------------------

def test_the_split_is_stable_and_sends_about_a_third_to_test():
    ids = ["session-%d" % n for n in range(3000)]
    first = [mjp.split_of(s) for s in ids]
    assert first == [mjp.split_of(s) for s in ids]
    share = first.count(mjp.TEST) / len(ids)
    assert 0.30 < share < 0.37


def test_labels_take_both_raters_and_skip_a_chunk_one_rater_never_answered():
    a, b = mjp.mpr.RATERS
    chunks = {"s": [{"id": "c1", "prompts": [{"key": "s#0"}, {"key": "s#1"}], "rules": ["r1", "r2"]},
                    {"id": "c2", "prompts": [{"key": "s#2"}], "rules": ["r3"]}]}
    col = lambda r: mjp.mrc.column(r, mjp.mpr.FREQ_SYSTEM)
    freq = {col(a): {"c1": {"s#0": ["r1", "r2"], "s#1": []}, "c2": {"s#2": ["r3"]}},
            col(b): {"c1": {"s#0": ["r1"], "s#1": ["r2"]}}}

    shown, named = mjp.labels_from(chunks, freq, [a, b])

    assert shown == {"s#0": {"r1", "r2"}, "s#1": {"r1", "r2"}}
    assert named == {"s#0": {"r1"}, "s#1": set()}


# --- the replay ----------------------------------------------------------------------

def test_a_pool_of_k_asks_only_the_first_k_rules_and_uses_the_shipped_bar():
    scores = {"a": 0.2, "b": 0.5, "c": 0.9}
    assert mjp.picks(scores, ["a", "b", "c"], 2, 0.4) == ["b"]
    assert mjp.picks(scores, ["a", "b", "c"], 3, 0.4) == ["c", "b"]
    assert mjp.picks(scores, ["a", "b", "c"], 3, 0.6) == ["c"]


def test_the_replay_dedupes_within_a_session_and_skips_unscored_and_pre_gated_prompts():
    prompts = [_prompt("s", 0, ["a", "b"]), _prompt("s", 1, ["a", "c"]), _prompt("s", 2, ["d"], tokens_ok=False),
               _prompt("s", 3, ["e"])]
    scores = {"s#0": _ok({"a": 0.9, "b": 0.1}), "s#1": _ok({"a": 0.9, "c": 0.8}),
              "s#2": _ok({"d": 0.9}), "s#3": {"status": "timeout", "ms": 30000, "scores": {}}}

    first = mjp.simulate(prompts, scores, 10, 0.4, cap=10)

    assert first == {("s", "a"): 0, ("s", "c"): 1}


def test_the_replay_applies_the_session_cap_before_each_prompt():
    prompts = [_prompt("s", 0, ["a", "b"]), _prompt("s", 1, ["c"])]
    scores = {"s#0": _ok({"a": 0.9, "b": 0.9}), "s#1": _ok({"c": 0.9})}

    assert set(mjp.simulate(prompts, scores, 10, 0.4, cap=2)) == {("s", "a"), ("s", "b")}
    assert set(mjp.simulate(prompts, scores, 10, 0.4, cap=3)) == {("s", "a"), ("s", "b"), ("s", "c")}


def test_injected_pairs_carry_the_prompt_key_of_the_first_injection():
    prompts = [_prompt("s", 0, ["a"]), _prompt("s", 1, ["a", "b"])]
    scores = {"s#0": _ok({"a": 0.9}), "s#1": _ok({"a": 0.9, "b": 0.9})}

    assert mjp.injected_pairs(prompts, scores, 10, 0.4, 10) == [("s", "s#0", "a"), ("s", "s#1", "b")]


# --- coverage --------------------------------------------------------------------------

def test_a_unit_is_reached_only_by_an_injection_at_or_before_a_prompt_it_applies_to():
    unit = {"session_id": "s", "slug": "a", "prompts": [2, 4]}
    assert mjp.reached(unit, {("s", "a"): 4})
    assert mjp.reached(unit, {("s", "a"): 0})
    assert not mjp.reached(unit, {("s", "a"): 5})
    assert not mjp.reached(unit, {})


def test_recorded_sessionstart_or_mcp_delivery_counts_unless_reading_the_reflex_alone():
    unit = {"session_id": "s", "slug": "a", "prompts": [1], "session_start": True, "mcp": False}
    assert mjp.reached(unit, {})
    assert not mjp.reached(unit, {}, reflex_only=True)


def test_the_baseline_is_what_520_recorded_on_the_units_native_memory_lacked():
    units = [{"redundant": False, "delivered": True}, {"redundant": False, "delivered": False},
             {"redundant": True, "delivered": True}]
    rec = mjp.recorded(units)
    assert (rec["k"], rec["n"]) == (1, 2)


# --- precision ---------------------------------------------------------------------------

def test_precision_keeps_unlabelled_pairs_apart_until_a_sample_rates_them():
    pairs = [("s", "s#0", "a"), ("s", "s#0", "b"), ("s", "s#1", "x"), ("s", "s#1", "y")]
    shown = {"s#0": {"a", "b"}}
    named = {"s#0": {"a"}}

    bare = mjp.precision(pairs, shown, named)
    assert (bare["labelled"], bare["labelled_relevant"], bare["unlabelled"]) == (2, 1, 2)
    assert bare["labelled_precision"] == 0.5
    assert "precision" not in bare

    rated = mjp.precision(pairs, shown, named, {"s#1|x": True, "s#1|y": False})
    assert rated["precision"] == (1 + 0.5 * 2) / 4


def test_the_sample_draws_only_unlabelled_pairs_of_the_test_split_and_is_seeded():
    test_sid = next(s for s in ("s%d" % n for n in range(100)) if mjp.split_of(s) == mjp.TEST)
    dev_sid = next(s for s in ("s%d" % n for n in range(100)) if mjp.split_of(s) == mjp.DEV)
    pairs = [(test_sid, test_sid + "#0", "seen"), (test_sid, test_sid + "#0", "new%d" % 0),
             (dev_sid, dev_sid + "#0", "new_dev")] + [(test_sid, test_sid + "#1", "new%d" % n) for n in range(1, 9)]
    shown = {test_sid + "#0": {"seen"}}

    drawn = mjp.draw_sample(pairs, shown, n=5)

    assert drawn == mjp.draw_sample(pairs, shown, n=5)
    assert len(drawn) == 5
    assert all(k.startswith(test_sid) and s.startswith("new") for k, s in drawn)


def test_sample_labels_need_every_rater_and_mark_what_both_named():
    a, b = mjp.mpr.RATERS
    chunks = [{"id": "c1", "prompts": [{"key": "s#0"}], "rules": ["x", "y"]},
              {"id": "c2", "prompts": [{"key": "s#1"}], "rules": ["z"]}]
    answers = {a: {"c1": {"s#0": ["x", "y"]}, "c2": {"s#1": ["z"]}}, b: {"c1": {"s#0": ["x"]}}}

    assert mjp.sample_labels(chunks, answers, [a, b]) == {"s#0|x": True, "s#0|y": False}


# --- choice and verdict --------------------------------------------------------------------

def _row(pool, at, k, prec):
    return {"pool": pool, "at": at, "coverage_lacked": {"k": k}, "precision": {"labelled_precision": prec}}


def test_dev_choice_is_the_widest_coverage_above_the_precision_bar_ties_to_smaller_pool_then_higher_bar():
    rows = [_row(50, 0.4, 90, 0.30), _row(25, 0.4, 80, 0.45), _row(10, 0.5, 80, 0.50), _row(10, 0.6, 80, 0.55),
            _row(3, 0.4, 40, 0.60)]
    assert (mjp.choose(rows)["pool"], mjp.choose(rows)["at"]) == (10, 0.6)
    assert mjp.choose([_row(50, 0.4, 90, 0.30)]) is None


def test_the_verdict_names_every_failed_condition():
    base = {"share": 0.25}
    assert mjp.verdict({"share": 0.55}, base, 0.45, 900)["pass"]
    failed = mjp.verdict({"share": 0.40}, base, 0.35, 1800)["failed"]
    assert len(failed) == 3
    assert any("coverage" in f for f in failed) and any("precision" in f for f in failed) \
        and any("latency" in f for f in failed)
    assert "precision not read yet (rater sample pending)" in mjp.verdict({"share": 0.6}, base, None, 900)["failed"]


# --- latency and drift ----------------------------------------------------------------------

def test_latency_reports_median_p90_and_the_share_past_the_hook_budget():
    s = mjp.latency_summary([100, 200, 300, 400, 500, 600, 700, 800, 900, 3000])
    assert s["median"] == 550 and s["p90"] == 3000 and s["over_budget"] == 0.1
    assert mjp.latency_summary([]) == {"n": 0}


def test_score_drift_compares_a_rule_asked_in_a_small_pool_with_the_full_request():
    latency = {"s#0": {"3": {"scores": {"a": 0.5, "b": 0.2}}}}
    scores = {"s#0": {"scores": {"a": 0.6, "b": 0.2, "c": 0.9}}}
    d = mjp.score_drift(latency, scores)
    assert d["pairs"] == 2 and abs(d["mean_abs"] - 0.05) < 1e-9 and abs(d["max_abs"] - 0.1) < 1e-9


# --- sending --------------------------------------------------------------------------------

def test_requests_stop_after_consecutive_failures_and_keep_what_arrived():
    got = {}
    rows = iter([{"status": "ok"}] + [{"status": "timeout"}] * 10)
    jobs = [("j%d" % n, lambda: next(rows)) for n in range(11)]

    state = mjp.run_requests(jobs, lambda jid, row: got.__setitem__(jid, row), workers=1, pause=0.0,
                             max_failures=3)

    assert state["stopped"] and state["done"] == 1 and state["failed"] == 3
    assert list(got) == ["j0"]


def test_sample_chunks_use_520s_shape_one_prompt_each():
    prompts = {"s#0": dict(_prompt("s", 0, []), answered="the agent said"), "s#1": _prompt("s", 1, [])}
    chunks = mjp.sample_chunks([("s#0", "b"), ("s#0", "a"), ("s#1", "c")], prompts)
    assert [c["rules"] for c in chunks] == [["a", "b"], ["c"]]
    assert chunks[0]["prompts"][0]["answered"] == "the agent said"
    assert all(len(c["prompts"]) == 1 for c in chunks)
