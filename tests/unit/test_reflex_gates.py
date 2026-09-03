from __future__ import annotations

import pytest

from mnemo.core.reflex.gates import (
    GateResult, evaluate_gates, DEFAULT_THRESHOLDS,
    idf_max, effective_absolute_floor,
)


def test_empty_scores_returns_silence_reason():
    res = evaluate_gates([], query_tokens=["x"], doc_tokens_by_slug={}, thresholds=DEFAULT_THRESHOLDS)
    assert res.accepted_slugs == []
    assert res.silence_reason == "index_missing"


def test_absolute_floor_failure():
    scores = [("a", 1.5)]
    res = evaluate_gates(scores, query_tokens=["prisma", "mock"],
                         doc_tokens_by_slug={"a": {"prisma", "mock"}},
                         thresholds=DEFAULT_THRESHOLDS)
    assert res.accepted_slugs == []
    assert res.silence_reason == "absolute_floor_fail"


def test_relative_gap_failure():
    scores = [("a", 3.0), ("b", 2.5)]  # ratio 1.2 < 1.5
    res = evaluate_gates(scores, query_tokens=["prisma", "mock", "orm"],
                         doc_tokens_by_slug={
                             "a": {"prisma", "mock", "orm"},
                             "b": {"prisma", "mock"},
                         },
                         thresholds=DEFAULT_THRESHOLDS)
    assert res.accepted_slugs == []
    assert res.silence_reason == "relative_gap_fail"


def test_term_overlap_failure():
    scores = [("a", 5.0)]
    res = evaluate_gates(scores, query_tokens=["foo", "bar", "baz"],
                         doc_tokens_by_slug={"a": {"foo"}},  # only 1 overlap
                         thresholds=DEFAULT_THRESHOLDS)
    assert res.accepted_slugs == []
    assert res.silence_reason == "term_overlap_fail"


def test_all_three_gates_pass_returns_top1():
    scores = [("a", 5.0), ("b", 2.0)]  # 5.0/2.0=2.5 >= 1.5
    res = evaluate_gates(scores, query_tokens=["prisma", "mock"],
                         doc_tokens_by_slug={
                             "a": {"prisma", "mock", "jest"},
                             "b": {"prisma"},
                         },
                         thresholds=DEFAULT_THRESHOLDS)
    assert res.accepted_slugs == ["a"]
    assert res.silence_reason is None


def test_top2_included_when_also_passes():
    scores = [("a", 5.0), ("b", 2.5)]
    res = evaluate_gates(scores, query_tokens=["prisma", "mock"],
                         doc_tokens_by_slug={
                             "a": {"prisma", "mock"},
                             "b": {"prisma", "mock"},
                         },
                         thresholds=DEFAULT_THRESHOLDS)
    # 5.0/2.5 = 2.0 >= relative_gap; b passes overlap + absolute_floor (2.0).
    assert res.accepted_slugs == ["a", "b"]


def test_top2_excluded_when_below_absolute_floor():
    scores = [("a", 5.0), ("b", 1.8)]  # 1.8 below 2.0 floor
    res = evaluate_gates(scores, query_tokens=["prisma", "mock"],
                         doc_tokens_by_slug={
                             "a": {"prisma", "mock"},
                             "b": {"prisma", "mock"},
                         },
                         thresholds=DEFAULT_THRESHOLDS)
    assert res.accepted_slugs == ["a"]


def test_idf_max_matches_bm25_laplace_idf():
    assert idf_max(1) == pytest.approx(0.2877, abs=1e-3)
    assert idf_max(30) == pytest.approx(3.0285, abs=1e-3)


def test_effective_floor_scales_below_reference_and_clamps_above():
    assert effective_absolute_floor(2.0, 1, 30) == pytest.approx(0.19, abs=0.01)
    assert effective_absolute_floor(2.0, 6, 30) == pytest.approx(1.02, abs=0.01)
    assert effective_absolute_floor(2.0, 30, 30) == 2.0
    assert effective_absolute_floor(2.0, 500, 30) == 2.0


def test_effective_floor_is_unscaled_without_doc_count_or_reference():
    assert effective_absolute_floor(2.0, None, 30) == 2.0
    assert effective_absolute_floor(2.0, 0, 30) == 2.0
    assert effective_absolute_floor(2.0, 5, 1) == 2.0
    assert effective_absolute_floor(2.0, 5, 0) == 2.0


def test_one_rule_vault_can_pass_the_floor():
    scores = [("a", 0.6)]
    res = evaluate_gates(
        scores,
        query_tokens=["add", "dependencies", "package"],
        doc_tokens_by_slug={"a": {"add", "dependencies", "package", "yarn"}},
        thresholds=DEFAULT_THRESHOLDS,
        doc_count=1,
    )
    assert res.accepted_slugs == ["a"]
    assert res.silence_reason is None
    assert res.effective_floor == pytest.approx(0.19, abs=0.01)


def test_without_doc_count_the_floor_is_not_scaled():
    scores = [("a", 0.6)]
    res = evaluate_gates(
        scores,
        query_tokens=["add", "dependencies", "package"],
        doc_tokens_by_slug={"a": {"add", "dependencies", "package", "yarn"}},
        thresholds=DEFAULT_THRESHOLDS,
    )
    assert res.silence_reason == "absolute_floor_fail"
    assert res.effective_floor == 2.0


def test_scaled_floor_still_applies_to_top2():
    scores = [("a", 0.6), ("b", 0.1)]
    res = evaluate_gates(
        scores,
        query_tokens=["add", "dependencies", "package"],
        doc_tokens_by_slug={
            "a": {"add", "dependencies", "package"},
            "b": {"add", "dependencies", "package"},
        },
        thresholds=DEFAULT_THRESHOLDS,
        doc_count=1,
    )
    assert res.accepted_slugs == ["a"]
