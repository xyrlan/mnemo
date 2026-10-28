"""``tools/measure_notice_bar.py`` over synthetic samples and labels (#789)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[2] / "tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _TOOLS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mnb = _load("measure_notice_bar")

NOTICE_TEXT = "<task-notification>\n<task-id>b1</task-id>\nexit code 1\n</task-notification>"


def _rated(kind: str, band: str, on_point: int, total: int, *, tag: str) -> list:
    """``total`` labelled pairs of one cell, one prompt each, ``on_point`` of them rated 2."""
    return [{"row": "%s-%s-%d" % (tag, band, i), "slug": "r", "kind": kind, "band": band,
             "label": 2 if i < on_point else 1}
            for i in range(total)]


def _cells(typed_near, typed_above, notice_near, notice_above, n=40) -> list:
    return (_rated(mnb.TYPED, mnb.NEAR, typed_near, n, tag="t")
            + _rated(mnb.TYPED, mnb.ABOVE, typed_above, n, tag="t")
            + _rated(mnb.NOTICE, mnb.NEAR, notice_near, n, tag="n")
            + _rated(mnb.NOTICE, mnb.ABOVE, notice_above, n, tag="n"))


def test_kind_is_the_shipped_classifier():
    assert mnb.kind_of(NOTICE_TEXT) == mnb.NOTICE
    assert mnb.kind_of("why did this fail?\n" + NOTICE_TEXT) == mnb.TYPED
    # #764 counted mnemo's wakes as notices; they are work orders here.
    assert mnb.kind_of('<mnemo-pr-follow pr="1">\nfix CI\n</mnemo-pr-follow>') == mnb.TYPED
    assert mnb.kind_of(None) is None


def test_notices_far_worse_everywhere_pick_skip():
    reading = mnb.bar(_cells(24, 32, 2, 4))
    assert reading["skip"]["verdict"] == mnb.SKIP
    assert reading["branch"] == mnb.SKIP


def test_notices_bad_only_near_the_bar_pick_raise():
    """#789's shape: typed 46%/66%, notices 20%/57%."""
    reading = mnb.bar(_cells(18, 26, 8, 23))
    assert reading["skip"]["verdict"] != mnb.SKIP
    assert reading["raise"]["verdict"] == mnb.RAISE
    assert reading["branch"] == mnb.RAISE


def test_notices_like_typed_prompts_leave_them():
    reading = mnb.bar(_cells(18, 26, 18, 26))
    assert reading["branch"] == mnb.LEAVE


def test_costs_count_bytes_and_weight_on_point_by_cell():
    population = [
        {"row": "a", "kind": mnb.TYPED, "band": mnb.NEAR, "bytes": 100},
        {"row": "b", "kind": mnb.NOTICE, "band": mnb.NEAR, "bytes": 300},
        {"row": "b", "kind": mnb.NOTICE, "band": mnb.ABOVE, "bytes": 200},
        {"row": "c", "kind": mnb.NOTICE, "band": mnb.mib.BELOW, "bytes": 400},
        {"row": "d", "kind": None, "band": mnb.NEAR, "bytes": 999},
    ]
    rates = {"typed/near": {"on_point": 0.5}, "typed/above": {"on_point": 0.7},
             "notice/near": {"on_point": 0.2}, "notice/above": {"on_point": 0.6}}
    out = mnb.costs(population, rates)
    assert out["total"]["bytes"] == 1000 and out["total"]["unmatched_pairs"] == 1
    assert abs(out["total"]["on_point"] - (0.5 + 0.2 + 0.6 + 0.2)) < 1e-9
    raised = out["branches"][mnb.RAISE]
    # The near notice and the one under the bar; the above one stays.
    assert raised["pairs"] == 2 and raised["bytes"] == 700 and raised["prompts"] == 2
    assert abs(raised["on_point_lost"] - 0.4) < 1e-9
    skipped = out["branches"][mnb.SKIP]
    assert skipped["pairs"] == 3 and skipped["bytes"] == 900
    assert out["branches"][mnb.LEAVE]["pairs"] == 0


def test_the_sample_takes_its_kind_from_the_whole_prompt_not_the_judges_cut():
    """The sample froze the judge's view, cut at 1200 characters; a long notice
    cut there has no closing tag, and must still count as a notice."""
    cut = NOTICE_TEXT[:30]
    assert mnb.kind_of(cut) == mnb.TYPED
    sample = [{"row": "n1", "slug": "r", "band": mnb.NEAR, "prompt": cut},
              {"row": "t1", "slug": "r", "band": mnb.NEAR, "prompt": "typed"}]
    population = [{"row": "n1", "slug": "r", "band": mnb.NEAR, "kind": mnb.NOTICE, "bytes": 1},
                  {"row": "t1", "slug": "r", "band": mnb.NEAR, "kind": mnb.TYPED, "bytes": 1}]
    labels = {"n1": {"r": 0}, "t1": {"r": 2}}
    data = mnb.report(population, sample, labels)
    assert data["cells"]["notice/near"] == {"n": 1, "on_point": 0.0}
    assert data["cells"]["typed/near"] == {"n": 1, "on_point": 1.0}
    assert data["notice_prompts"] == 1 and data["prompts"] == 2
    text = mnb.format_report(data)
    assert "branch:" in text and "notice/near" in text
