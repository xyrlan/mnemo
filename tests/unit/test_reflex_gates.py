from __future__ import annotations

from mnemo.core.reflex.gates import (
    GateResult, evaluate_gates, DEFAULT_THRESHOLDS,
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
