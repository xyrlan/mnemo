"""Frozen-set evaluator for BM25F config scoring.

Delegates actual BM25F scoring to mnemo.core.reflex.bm25.score_docs.
The index is built externally and passed in via an index_factory callable
so tests can inject a synthetic index without touching the filesystem.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Case:
    """A single recall evaluation case."""
    id: str
    project: str
    topic: str
    expect_slug: str


@dataclass
class ScoreReport:
    """Aggregate metrics from scoring a config against recall cases."""
    primacy_at_5: float      # fraction where expect_slug is in top-5
    mrr: float               # mean reciprocal rank (0 if not in top-10)
    p95_latency_ms: float    # p95 per-query latency in milliseconds
    n_cases: int


def _reciprocal_rank(ranked: list[tuple[str, float]], expect_slug: str, k: int = 10) -> float:
    for i, (slug, _score) in enumerate(ranked[:k]):
        if slug == expect_slug:
            return 1.0 / (i + 1)
    return 0.0


def score_config(
    config: "BM25Config",  # type: ignore[name-defined]  # forward ref
    *,
    cases: list[Case],
    index_factory: Callable[[str, list[str]], dict],
    query_overrides: Optional[dict[str, list[str]]] = None,
) -> ScoreReport:
    """Score a BM25Config against a list of recall Cases.

    Args:
        config: The BM25Config to evaluate.
        cases: List of recall cases (expect_slug, project, topic, id).
        index_factory: Called with (project, query_tokens) → index dict.
            The index must be a reflex-index-compatible dict with
            ``postings``, ``docs``, ``avg_field_length``, ``doc_count``.
        query_overrides: Optional mapping of case.id → query_tokens,
            for test injection. When not provided, `topic` is used as query.

    Returns:
        ScoreReport with primacy@5, MRR, p95 latency, n_cases.
    """
    from mnemo.core.reflex.bm25 import score_docs
    from mnemo.core.reflex.tokenizer import tokenize

    if not cases:
        return ScoreReport(primacy_at_5=0.0, mrr=0.0, p95_latency_ms=0.0, n_cases=0)

    weights = {
        "name": config.weights.get("name", 3.0),
        "topic_tags": config.weights.get("topic_tags", 3.0),
        "aliases": config.weights.get("aliases", 2.5),
        "description": config.weights.get("description", 2.0),
        "body": config.weights.get("body", 1.0),
    }
    params = {"k1": config.k1, "b": config.b}

    in_top5 = 0
    rr_sum = 0.0
    latencies: list[float] = []

    for case in cases:
        if query_overrides and case.id in query_overrides:
            query_tokens = query_overrides[case.id]
        else:
            query_tokens = tokenize(case.topic)

        index = index_factory(case.project, query_tokens)
        candidate_slugs = list(index.get("docs", {}).keys())

        t0 = time.perf_counter()
        ranked = score_docs(
            index,
            query_tokens=query_tokens,
            candidate_slugs=candidate_slugs,
            weights=weights,
            params=params,
        )
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000.0)

        ranked_slugs = [slug for slug, _ in ranked]
        if case.expect_slug in ranked_slugs[:5]:
            in_top5 += 1
        rr_sum += _reciprocal_rank(ranked, case.expect_slug)

    n = len(cases)
    primacy = in_top5 / n
    mrr = rr_sum / n

    latencies.sort()
    p95_idx = max(0, int(0.95 * len(latencies)) - 1)
    p95_ms = latencies[p95_idx] if latencies else 0.0

    return ScoreReport(
        primacy_at_5=primacy,
        mrr=mrr,
        p95_latency_ms=p95_ms,
        n_cases=n,
    )
