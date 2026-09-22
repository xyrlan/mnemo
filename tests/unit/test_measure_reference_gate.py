"""``tools/measure_reference_gate.py`` over synthetic rules and synthetic labels.

No model is called: the judge is a function the test hands in. What is pinned:
the split, the consensus rule, the arithmetic of the table, that scoring uses
the stage's own prompt and parse and resumes, that a new prompt is a new
column, and that the tool holds exactly the rows the stage would.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from mnemo.core.extract import reference_gate as gate
from mnemo.core.extract.inbox.types import ExtractedPage

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_reference_gate.py"
_spec = importlib.util.spec_from_file_location("measure_reference_gate", _TOOL)
mrg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mrg)


def test_the_split_is_the_ids_parity():
    assert mrg.part_of("0000000a") == "dev"
    assert mrg.part_of("0000000b") == "test"


def test_consensus_needs_both_raters_on_the_same_side():
    assert mrg.consensus("G", "N") == mrg.JUNK
    assert mrg.consensus("S", "T") == mrg.GOOD
    assert mrg.consensus("G", "T") is None
    assert mrg.consensus("S", None) is None


def test_labels_read_either_file_shape():
    assert mrg._labels({"a": {"cat": "g"}}) == {"a": "G"}
    assert mrg._labels([["a", {"cat": "S", "inject": True}]]) == {"a": "S"}


def test_confusion_and_summary_arithmetic():
    truth = {"1": "junk", "2": "junk", "3": "good", "4": "good", "5": "good"}
    held = {"1": True, "2": False, "3": True, "4": False, "5": False}
    c = mrg.confusion(truth, held, truth)
    assert c == {"junk_held": 1, "junk_live": 1, "good_held": 1, "good_live": 2}
    s = mrg.summarize(c)
    assert s["junk_recall"] == 0.5
    assert s["good_lost"] == 1 / 3
    assert s["hold_precision"] == 0.5
    assert s["live_junk_share"] == 1 / 3
    assert s["base_junk_share"] == 2 / 5


def test_confusion_skips_rows_without_truth_or_answer():
    c = mrg.confusion({"1": "junk"}, {"2": True}, ["1", "2", "3"])
    assert sum(c.values()) == 0
    assert mrg.summarize(c)["junk_recall"] is None


def test_score_asks_with_the_stages_prompt_and_resumes():
    sample = [{"id": "%02x" % i, "text": "rule %d" % i} for i in range(13)]
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        n = prompt.count("\n\n[")
        return json.dumps({"verdicts": [{"i": i, "cat": "S"} for i in range(1, n + 1)]})

    first = mrg.score(sample[:5], {}, ask, gate)
    assert first == {r["id"]: "S" for r in sample[:5]}
    assert prompts == [gate.build_prompt(["rule %d" % i for i in range(5)])]

    prompts.clear()
    merged = mrg.score(sample, first, ask, gate)
    assert len(merged) == 13
    # Only the 8 unanswered rows were asked, CHUNK at a time.
    assert len(prompts) == 1 and "rule 4" not in prompts[0] and "rule 12" in prompts[0]


def test_a_new_prompt_is_a_new_column():
    assert mrg.column("m", "one") != mrg.column("m", "two")
    assert mrg.column("m", gate.SYSTEM_PROMPT).startswith("m@")


def test_the_tool_holds_exactly_what_the_stage_holds(tmp_path):
    verdicts = {"a": "G", "b": "T", "c": "S", "d": "N", "e": None}
    tool = mrg.held_by_verdict(verdicts, gate.KEEP)

    order = sorted(verdicts)
    text = json.dumps({"verdicts": [
        {"i": n, "cat": verdicts[k]} for n, k in enumerate(order, 1) if verdicts[k]
    ]})
    pages = [ExtractedPage(slug=k, type="reference", name=k, description="",
                           body="b", source_files=["s"], source_hash="h")
             for k in order]
    stage = {p.slug: gate.held(p, tmp_path) for p in gate.judge_pages(pages, lambda _: text)}
    assert stage == tool == {"a": True, "b": False, "c": False, "d": True, "e": True}


def test_report_lines_carry_every_signal_and_slice():
    truth = {"0a": "junk", "0b": "good"}
    parts = {i: mrg.part_of(i) for i in truth}
    lines = mrg.report_lines(truth, {"x": {"0a": True, "0b": False}}, parts, {"0a"})
    assert len(lines) == 1 + 4
    test_row = [l for l in lines if " test " in l][0]
    assert "0/0" in test_row  # junk row 0a is dev
    all_row = [l for l in lines if " all " in l and "reference" not in l][0]
    assert "1/1" in all_row and "0/1" in all_row
