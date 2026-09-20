"""``tools/measure_reflex_gate.py`` over synthetic units and synthetic labels.

Nothing here opens a socket: the judge is a function the test hands in, and
the dry run is pinned to build no request at all. What is pinned is what a
measurement tool has to be right about — the id a label is filed under, the
seeded draw, the arithmetic of the gate table, the fitted bar and the AUC —
and, above all, that the gate it grades is the one
``mnemo.core.reflex.judge`` ships:
``test_the_tool_and_the_stage_agree_on_the_gate`` runs both over the same
numbers and compares.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from mnemo.core.reflex import judge

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_reflex_gate.py"
_spec = importlib.util.spec_from_file_location("measure_reflex_gate", _TOOL)
mrg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mrg)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test reached the network")
    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.delenv(judge.DEFAULT_KEY_ENV, raising=False)


def _unit(uid, *candidates, fired=None, prompt="how do I deploy the worker"):
    """``candidates`` are ``(slug, bm25f, accepted)``."""
    cands = [{"slug": s, "bm25f": b, "accepted": a, "text": "rule text for " + s}
             for s, b, a in candidates]
    return {"uid": uid, "project": "p", "ts": 1.0, "session_id": "sid",
            "prompt": prompt, "prompt_chars": len(prompt),
            "silence_reason": None if any(c["accepted"] for c in cands) else "absolute_floor_fail",
            "fired": any(c["accepted"] for c in cands) if fired is None else fired,
            "floor": 2.0, "candidates": cands}


# --- identity and the split --------------------------------------------------

def test_the_id_is_the_identity_of_one_typed_prompt():
    args = ("sid-1", 1785788771.862, "fix the ledger race")
    assert mrg.unit_id(*args) == mrg.unit_id(*args)
    assert len(mrg.unit_id(*args)) == 10
    assert mrg.unit_id("sid-2", *args[1:]) != mrg.unit_id(*args)
    assert mrg.unit_id(args[0], 1785788771.863, args[2]) != mrg.unit_id(*args)


def test_only_the_first_200_characters_of_a_prompt_are_part_of_the_id():
    """A long prompt's tail must not change the id — it is an identity, not a
    checksum, and the stored text is truncated anyway."""
    head = "x" * 200
    assert mrg.unit_id("s", 1.0, head + "aaa") == mrg.unit_id("s", 1.0, head + "bbb")


def test_the_split_is_a_function_of_the_id():
    assert mrg.part_of("0" * 10) == "test"
    assert mrg.part_of("1" * 10) == "dev"


# --- the seeded draw ---------------------------------------------------------

def test_the_draw_is_stratified_and_reproducible():
    units = ([_unit("f%02d" % i, ("a", 9.0, True)) for i in range(50)]
             + [_unit("s%02d" % i, ("a", 0.1, False)) for i in range(50)])
    first = mrg.draw(units, fired=20, silenced=10, seed=3)
    again = mrg.draw(units, fired=20, silenced=10, seed=3)
    assert first == again
    assert len(first) == 30
    assert sum(1 for uid in first if uid.startswith("f")) == 20
    assert sum(1 for uid in first if uid.startswith("s")) == 10
    assert mrg.draw(units, fired=20, silenced=10, seed=4) != first


# --- the table ---------------------------------------------------------------

LABELS = {
    "a1": {"a": 2, "b": 0, "c": 0},
    "a2": {"a": 0, "b": 1},
}
UNITS = [
    _unit("a1", ("a", 9.0, True), ("b", 8.0, True), ("c", 1.0, False)),
    _unit("a2", ("a", 3.0, True), ("b", 2.0, False)),
]


def test_the_shipped_row_counts_what_the_gate_accepted():
    row = mrg.gate_row("shipped", UNITS, LABELS, mrg.shipped_slugs)
    assert row["injected"] == 3
    assert row["prompts"] == 2 and row["of_prompts"] == 2
    assert row["on_point_kept"] == 1 and row["on_point_total"] == 1
    # a, b of a1 and a of a2: one on-point, two noise.
    assert row["on_point"] == pytest.approx(1 / 3, abs=1e-4)
    assert row["noise"] == pytest.approx(2 / 3, abs=1e-4)


def test_the_every_top_3_row_is_the_whole_pool():
    row = mrg.gate_row("every top 3", UNITS, LABELS, mrg.all_slugs)
    assert row["injected"] == 5 and row["prompts"] == 2
    assert row["on_point_kept"] == row["on_point_total"] == 1


def test_a_gate_that_injects_nothing_fires_on_no_prompt():
    row = mrg.gate_row("silent", UNITS, LABELS, lambda u: [])
    assert row["injected"] == 0 and row["prompts"] == 0
    # No labelled rule survives, so the shares are 0 rather than a ZeroDivision.
    assert row["noise"] == row["on_point"] == 0.0
    assert row["on_point_kept"] == 0 and row["on_point_total"] == 1


def test_the_tool_and_the_stage_agree_on_the_gate():
    """The whole point: a report that grades a rule the hook would not inject
    is grading something that does not ship."""
    scores = {"a": 0.91, "b": 0.6, "c": 0.12}
    unit = _unit("u", ("a", 9.0, True), ("b", 8.0, False), ("c", 1.0, False))
    for at in (0.0, 0.12, 0.5, 0.6, 0.7, 1.0):
        assert mrg.over_bar(unit, scores, at) == judge.chosen(scores, at)


def test_a_candidate_the_judge_never_scored_is_not_injected():
    unit = _unit("u", ("a", 9.0, True), ("b", 8.0, False))
    assert mrg.over_bar(unit, {"a": 0.9}, 0.6) == ["a"]


# --- the fitted bar and the AUC ---------------------------------------------

def test_the_fitted_bar_is_the_loosest_one_that_keeps_the_recall():
    scored = [(0.9, True), (0.8, True), (0.7, True), (0.6, True), (0.5, True),
              (0.4, True), (0.3, True), (0.2, True), (0.1, True), (0.05, True),
              (0.9, False)]
    # 10 positives, 90% recall needs 9 of them: the loosest bar keeping 9 is 0.1.
    assert mrg.fit_bar(scored, recall=0.9) == 0.1
    assert mrg.fit_bar(scored, recall=1.0) == 0.05


def test_the_fitted_bar_is_none_with_nothing_to_fit_on():
    assert mrg.fit_bar([(0.5, False)]) is None


def test_a_perfect_signal_scores_one_and_a_useless_one_a_half():
    assert mrg.auc([(1.0, True), (0.9, True), (0.1, False)]) == 1.0
    assert mrg.auc([(0.5, True), (0.5, False)]) == 0.5
    assert mrg.auc([(0.1, True), (0.9, False)]) == 0.0
    assert mrg.auc([(0.1, True)]) is None


def test_the_report_puts_every_split_side_by_side():
    scores = {u["uid"]: {c["slug"]: 0.9 if LABELS[u["uid"]].get(c["slug"]) == 2 else 0.1
                         for c in u["candidates"]} for u in UNITS}
    data = mrg.report(UNITS, LABELS, scores, bars=[0.6])
    assert set(data["parts"]) == {"all", "dev", "test"}
    assert data["dev"] + data["test"] == len(UNITS)
    gates = [row["gate"] for row in data["parts"]["all"]]
    assert gates[0] == "shipped" and gates[1] == "every top 3"
    assert any(g.startswith("judge >=") for g in gates)
    # With a perfectly separating signal the judge row keeps the on-point rule
    # and injects nothing else.
    row = [r for r in data["parts"]["all"] if r["gate"].startswith("judge >=")][0]
    assert row["injected"] == 1 and row["on_point_kept"] == 1
    assert mrg.format_report(data, rater="r", blind=True)


# --- the blind round ---------------------------------------------------------

def test_a_blind_chunk_carries_the_prompt_and_the_rule_and_nothing_else():
    chunks, ids = mrg.blind_chunks(UNITS, {})
    assert len(ids) == 5
    blob = json.dumps(chunks)
    for unit in UNITS:
        assert unit["uid"] not in blob
    assert "bm25f" not in blob and "accepted" not in blob
    assert set(chunks[0][0]) == {"message", "rules"}


def test_a_pair_already_labelled_is_not_asked_again():
    _chunks, ids = mrg.blind_chunks(UNITS, {"a1": {"a": 2, "b": 0, "c": 0}})
    assert sorted(slug for _uid, slug in ids.values()) == ["a", "b"]


def test_labels_are_folded_back_or_nothing_is_written():
    ids = {"0": ["a1", "a"], "1": ["a1", "b"]}
    folded, problems = mrg.imported_labels(ids, {"0": 2, "1": 0})
    assert folded == {"a1": {"a": 2, "b": 0}} and problems == []
    _folded, problems = mrg.imported_labels(ids, {"0": 2})
    assert problems == ["no label for id 1"]
    _folded, problems = mrg.imported_labels(ids, {"0": 3, "1": 0})
    assert problems == ["id 0: 3 is not 0, 1 or 2"]


def test_scoring_resumes_where_it_stopped():
    unit = UNITS[0]
    assert mrg.pending_chunks(unit, {}) == [["a", "b", "c"]]
    assert mrg.pending_chunks(unit, {"a": 0.9}) == [["b", "c"]]
    # Membership, not truthiness: an honest 0.0 must not be re-asked forever.
    assert mrg.pending_chunks(unit, {"a": 0.9, "b": 0.1, "c": 0.0}) == []


def test_a_score_file_written_as_a_list_still_reads():
    """An early run filed one entry per request; the shipped shape is a number."""
    assert mrg._normalise_scores({"u": {"a": [0.1, 0.8], "b": 0.3, "c": "x"}}) == {
        "u": {"a": 0.8, "b": 0.3}}


# --- the prompt the tool stores is the one the stage would send --------------

def test_the_stored_prompt_is_the_state_the_stage_sends():
    text = "first line\n\n   second   line " + "y" * 4000
    assert mrg.judge_state(text) == judge.state(text)["developer_message"]
    assert len(mrg.judge_state(text)) == judge.PROMPT_CHARS


# --- what the vault had at the time -----------------------------------------

def test_a_rule_learned_after_the_prompt_is_not_a_candidate():
    """Today's vault firing on a rule extracted from the answer to that very
    prompt is hindsight, not retrieval."""
    scores = [("old", 9.0), ("later", 8.0), ("older", 7.0)]
    learned = {"old": 50.0, "later": 200.0, "older": 10.0}
    assert mrg.held_at(scores, learned, 100.0) == [("old", 9.0), ("older", 7.0)]


def test_an_undated_rule_is_not_a_candidate_either():
    """Undatable is not evidence of having been there."""
    assert mrg.held_at([("a", 1.0)], {"a": None}, 100.0) == []
    assert mrg.held_at([("a", 1.0)], {}, 100.0) == []


def test_the_top_is_taken_after_the_filter_not_before():
    """Otherwise a rule that could not have been injected pushes down one
    that could."""
    scores = [("f1", 9.0), ("f2", 8.0), ("f3", 7.0), ("ok", 6.0)]
    learned = {"f1": 200.0, "f2": 200.0, "f3": 200.0, "ok": 10.0}
    assert mrg.held_at(scores, learned, 100.0, top=3) == [("ok", 6.0)]
