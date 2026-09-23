"""``tools/measure_reflex_reach.py`` over synthetic units.

No model is called. What is pinned is what the measurement has to be right
about: that a prompt's loss is charged to the first stage that loses it, that
the session replay is the hook's cap and dedupe, that the judge rows use the
shipped ``judge.chosen`` and give a range when Jev never scored a pair, that
the rater sees no slug or rank, and that an interrupted run resumes.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_reflex_reach.py"
_spec = importlib.util.spec_from_file_location("measure_reflex_reach", _TOOL)
mrr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mrr)


def _unit(uid, slugs, *, shipped=(), fate=None, fired=True, prompt="fix the migration"):
    return {"uid": uid, "prompt": prompt, "stratum_fired": fired,
            "pool": [{"slug": s, "rank": i + 1, "text": "rule " + s} for i, s in enumerate(slugs)],
            "shipped": list(shipped), "fate": fate if fate is not None else {s: "injected" for s in shipped}}


# --- the pool ------------------------------------------------------------------

def test_the_pool_drops_rules_learned_after_the_prompt_before_cutting_at_ten():
    scores = [("late", 9.0)] + [("r%d" % i, 8.0 - i) for i in range(12)]
    learned = {"late": 200.0, **{"r%d" % i: 50.0 for i in range(12)}}

    pool = mrr.pool_of(scores, learned, 100.0)

    assert [s for s, _ in pool] == ["r%d" % i for i in range(10)]


def test_the_shipped_gate_counts_only_what_the_vault_held_in_rank_order():
    assert mrr.shipped_of(["b", "late", "a"], ["a", "b", "c"]) == ["a", "b"]


# --- the session replay ----------------------------------------------------------

def test_dedupe_is_a_rule_already_injected_earlier_in_the_session():
    fates = mrr.replay_session([("p1", ["a", "b"]), ("p2", ["b", "c"])], cap=20)

    assert fates == {"p1": {"a": "injected", "b": "injected"},
                     "p2": {"b": "deduped", "c": "injected"}}


def test_the_cap_is_checked_before_the_prompt_so_one_prompt_can_pass_it():
    fates = mrr.replay_session([("p1", ["a"]), ("p2", ["b", "c"]), ("p3", ["d"])], cap=2)

    assert fates["p2"] == {"b": "injected", "c": "injected"}
    assert fates["p3"] == {"d": "cap"}


# --- the stage a prompt loses its rule at --------------------------------------------

def test_a_prompt_with_no_on_point_rule_is_not_in_the_denominator():
    unit = _unit("u", ["a", "b"], shipped=["a"])
    assert mrr.stage(unit, {"a": 1, "b": 0}, ["a"]) is None


def test_an_on_point_rule_only_below_the_top_three_is_lost_to_ranking():
    unit = _unit("u", ["a", "b", "c", "d"], shipped=["a"])
    assert mrr.stage(unit, {"a": 0, "b": 1, "c": 0, "d": 2}, ["a"]) == "ranking"


def test_an_on_point_rule_in_the_top_three_the_gate_skipped_is_lost_to_the_gate():
    unit = _unit("u", ["a", "b", "c", "d"], shipped=["a"])
    assert mrr.stage(unit, {"a": 0, "b": 2, "c": 0, "d": 2}, ["a"]) == "gate"


def test_an_injected_on_point_rule_is_reached_even_when_another_was_lost():
    unit = _unit("u", ["a", "b", "c", "d"], shipped=["a"])
    assert mrr.stage(unit, {"a": 2, "b": 0, "c": 0, "d": 2}, ["a"]) == "reached"


def test_the_session_replay_charges_its_own_losses_cap_before_dedupe():
    unit = _unit("u", ["a", "b", "c"], shipped=["a", "b"], fate={"a": "deduped", "b": "cap"})
    label = {"a": 2, "b": 2, "c": 0}

    assert mrr.stage(unit, label, [], gated=["a", "b"], fate=unit["fate"]) == "cap"
    assert mrr.stage(unit, {"a": 2, "b": 0, "c": 0}, [], gated=["a", "b"],
                     fate=unit["fate"]) == "deduped"


# --- the judge -----------------------------------------------------------------------

def test_the_judge_reads_only_the_top_three_at_its_bar():
    unit = _unit("u", ["a", "b", "c", "d"])
    jev = {"a": 0.9, "b": 0.5, "c": 0.65, "d": 0.99}

    assert mrr.judge_kept(unit, jev, 0.6, unscored=False) == ["a", "c"]


def test_a_pair_jev_never_scored_gives_a_range():
    unit = _unit("u", ["a", "b", "c"])
    jev = {"a": 0.1, "c": 0.2}

    assert mrr.judge_kept(unit, jev, 0.6, unscored=False) == []
    assert mrr.judge_kept(unit, jev, 0.6, unscored=True) == ["b"]


# --- the rater -----------------------------------------------------------------------

def test_batches_never_split_a_prompt_and_skip_what_is_labelled():
    units = [_unit("u%d" % i, ["s%d" % j for j in range(4)]) for i in range(3)]
    done = {"u0": {"s0": 0, "s1": 0, "s2": 0, "s3": 2}, "u1": {"s0": 1}}

    got = mrr.batches(units, done, size=5)

    assert [[(e["uid"], [s for s, _ in e["rules"]]) for e in b] for b in got] == [
        [("u1", ["s1", "s2", "s3"])], [("u2", ["s0", "s1", "s2", "s3"])]]


def test_the_rater_sees_the_message_and_rule_text_and_nothing_that_names_them():
    unit = _unit("abc123", ["mnemo__secret-slug", "other"])
    batch = mrr.batches([unit], {})[0]

    text = mrr.rater_prompt(batch)

    assert "fix the migration" in text and "[1] rule mnemo__secret-slug" in text
    assert "abc123" not in text and "rank" not in text and "bm25" not in text.lower()


def test_labels_fold_back_by_number_and_leave_illegible_ones_pending():
    batch = mrr.batches([_unit("u", ["a", "b", "c", "d"])], {})[0]
    reply = 'Sure:\n{"1": 2, "2": "1", "3": 7, "4": true}'

    assert mrr.parse_labels(reply, batch) == {"u": {"a": 2, "b": 1}}
    assert mrr.parse_labels("no json here", batch) == {}


def test_the_rater_column_changes_when_the_instruction_does(monkeypatch):
    before = mrr.column("claude-sonnet-5")
    monkeypatch.setattr(mrr, "RATER_SYSTEM", mrr.RATER_SYSTEM + " ")
    assert mrr.column("claude-sonnet-5") != before


# --- the numbers ---------------------------------------------------------------------

def test_weights_undo_the_fired_silenced_stratification():
    units = [_unit("a", ["x"], fired=True), _unit("b", ["x"], fired=True),
             _unit("c", ["x"], fired=False)]

    w = mrr.weights(units, {"fired": 10, "silenced": 30})

    assert w == {"a": 5.0, "b": 5.0, "c": 30.0}


def test_wilson_is_the_textbook_interval():
    lo, hi = mrr.wilson(5, 10)
    assert round(lo, 3) == 0.237 and round(hi, 3) == 0.763
    assert mrr.wilson(0, 0) == (None, None)


def test_agreement_counts_on_point_kappa_over_shared_pairs_only():
    a = {"u": {"x": 2, "y": 0, "z": 2, "only_a": 1}}
    b = {"u": {"x": 2, "y": 0, "z": 1}}

    got = mrr.agreement(a, b)

    assert got["n"] == 3 and got["matrix"][2][2] == 1 and got["matrix"][2][1] == 1
    assert round(got["exact"], 3) == 0.667
    assert round(got["kappa_on_point"], 3) == 0.4


def test_the_report_reads_reach_per_gate_on_one_synthetic_vault():
    units = [
        # shipped injects the on-point rule
        _unit("u1", ["a", "b", "c", "d"], shipped=["a"]),
        # on-point at rank 2, shipped takes rank 1 only; the judge takes it
        _unit("u2", ["a", "b", "c", "d"], shipped=["a"]),
        # on-point only at rank 4
        _unit("u3", ["a", "b", "c", "d"], shipped=["a"]),
        # shipped picks it, the session had hit the cap
        _unit("u4", ["a", "b", "c"], shipped=["a"], fate={"a": "cap"}),
        # no on-point rule anywhere
        _unit("u5", ["a", "b", "c"], shipped=["a"]),
    ]
    labels = {
        "u1": {"a": 2, "b": 0, "c": 0, "d": 0},
        "u2": {"a": 0, "b": 2, "c": 0, "d": 0},
        "u3": {"a": 0, "b": 0, "c": 0, "d": 2},
        "u4": {"a": 2, "b": 0, "c": 0},
        "u5": {"a": 0, "b": 1, "c": 0},
    }
    jev = {"u1": {"a": 0.9, "b": 0.1, "c": 0.1},
           "u2": {"a": 0.1, "b": 0.8, "c": 0.1},
           "u3": {"a": 0.1, "b": 0.1, "c": 0.1},
           "u4": {"a": 0.7, "b": 0.1}}   # u4's c never scored

    data = mrr.report(units, labels, jev, {"fired": 5, "silenced": 0})
    rows = data["rows"]

    assert data["with_on_point"] == 4 and data["with_on_point_top3"] == 3
    assert data["on_point_by_rank"][:4] == [2, 1, 0, 1]
    assert rows["shipped, gate only"]["counts"] == {
        "reached": 2, "cap": 0, "deduped": 0, "gate": 1, "ranking": 1}
    assert rows["shipped, with cap/dedupe"]["counts"] == {
        "reached": 1, "cap": 1, "deduped": 0, "gate": 1, "ranking": 1}
    assert rows["judge >= 0.6, low"]["counts"]["reached"] == 3
    assert rows["judge >= 0.6, low"]["counts"]["ranking"] == 1
    assert rows["judge >= 0.7, low"]["counts"]["reached"] == 3
    assert rows["judge >= 0.7, low"]["reach"] == 0.75
    assert data["unscored_top3"] == 4  # u4 c, and all three of u5
    assert data["judge_keep"]["0.6"] == (3, 3)

    levers = mrr.lever_values(data)
    assert levers["judge default-on (injectAt 0.6)"] == 0.25   # 3/4 against 2/4 gate-only
    assert levers["lift the session cap (shipped)"] == 0.25    # u4
    assert levers[mrr.PROJECTED] == 0.25                        # 1/4 ranking x 3/3 kept

    lines = mrr.report_lines(data, "claude-sonnet-5")
    assert lines[-1].startswith("recommendation: ")


def test_the_recommendation_is_the_best_worst_case_and_names_rater_flips():
    # Two prompts, on-point at rank 2 under one rater and rank 1 under the other.
    units = [_unit("u1", ["a", "b", "c"], shipped=["a"]),
             _unit("u2", ["a", "b", "c"], shipped=["a"])]
    sonnet = {"u1": {"a": 0, "b": 2, "c": 0}, "u2": {"a": 0, "b": 2, "c": 0}}
    fable = {"u1": {"a": 2, "b": 0, "c": 0}, "u2": {"a": 2, "b": 0, "c": 0}}
    jev = {"u1": {"a": 0.5, "b": 0.9, "c": 0.1}, "u2": {"a": 0.5, "b": 0.9, "c": 0.1}}

    data = mrr.report(units, sonnet, jev, {"fired": 2, "silenced": 0}, fable=fable)
    line = mrr.recommendation(data)

    # sonnet: judge@0.6 reaches b (+100 pp over shipped's a); fable: judge@0.6
    # drops a (-100 pp). At 0.4 the judge keeps both, so it wins everywhere.
    assert line.startswith("judge default-on (injectAt 0.4): at least +0.0 pp")
    assert "sign depends on the rater: judge default-on (injectAt 0.6)" in line


def test_a_prompt_with_an_unlabelled_pair_is_left_out_of_every_row():
    units = [_unit("u1", ["a", "b"], shipped=["a"])]
    data = mrr.report(units, {"u1": {"a": 2}}, {}, {"fired": 1, "silenced": 0})

    assert data["labelled_prompts"] == 0 and data["rows"]["shipped, gate only"]["n"] == 0


# --- the run -------------------------------------------------------------------------

def test_a_run_interrupted_after_one_call_resumes_at_the_next(tmp_path, monkeypatch):
    from mnemo.core import config, llm, paths

    vault = tmp_path / "vault"
    out = vault / ".mnemo" / mrr.OUT_DIR
    out.mkdir(parents=True)
    units = [_unit("u%d" % i, ["s%d" % j for j in range(10)]) for i in range(5)]
    (out / mrr.POOLS_NAME).write_text(json.dumps({
        "units": units, "missing": [], "population": {"fired": 5, "silenced": 0},
        "index_doc_count": 1, "cap": 20}), encoding="utf-8")
    monkeypatch.setattr(config, "load_config", lambda: {"extraction": {"subprocessTimeout": 5}})
    monkeypatch.setattr(paths, "vault_root", lambda cfg: vault)

    asked = []

    class Resp:
        total_cost_usd = 0.01

        def __init__(self, text):
            self.text = text

    def provider(prompt, *, system, model, timeout):
        asked.append(prompt)
        if len(asked) == 2:
            raise RuntimeError("rate limited")
        n = prompt.count("\n[")
        return Resp(json.dumps({str(i): 2 for i in range(1, n + 1)}))

    monkeypatch.setattr(llm, "resolve", lambda cfg: provider)

    try:
        mrr.main(["--send"])
    except RuntimeError:
        pass
    saved = json.loads((out / mrr.LABELS_NAME).read_text(encoding="utf-8"))
    col = mrr.column(mrr.DEFAULT_RATER)
    # four units of ten fill the first call of 40; the second call failed
    assert sorted(saved[col]) == ["u0", "u1", "u2", "u3"]

    asked.clear()
    assert mrr.main(["--send"]) == 0
    assert len(asked) == 1 and "rule s0" in asked[0]  # only u4 is asked again
    saved = json.loads((out / mrr.LABELS_NAME).read_text(encoding="utf-8"))
    assert sum(len(r) for r in saved[col].values()) == 50

    asked.clear()
    assert mrr.main(["--send"]) == 0
    assert asked == []  # nothing left to ask
