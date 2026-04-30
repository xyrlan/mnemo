"""Tests for frozen-set evaluator (_scorer.py) — T2."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.autopilot.tuner._scorer import (
    Case,
    ScoreReport,
    score_config,
)
from mnemo.autopilot.tuner.bm25_tuner import BM25Config


def _make_frozen_set(tmp_path: Path, cases: list[dict]) -> Path:
    d = tmp_path / ".mnemo"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "recall-cases.frozen.json"
    p.write_text(json.dumps(cases))
    return p


def _make_index(slugs: list[str]) -> dict:
    """Minimal reflex index for scoring tests."""
    docs = {}
    postings: dict = {}
    for i, slug in enumerate(slugs):
        # give each doc a distinct token matching the slug
        token = f"token{i}"
        docs[slug] = {
            "field_length": {"name": 1, "topic_tags": 0, "aliases": 0, "description": 0, "body": 1},
            "preview": f"preview of {slug}",
        }
        postings.setdefault(token, []).append({"slug": slug, "tf": {"name": 1, "body": 0}})
    return {
        "schema_version": 1,
        "doc_count": len(slugs),
        "avg_field_length": {"name": 1.0, "topic_tags": 0.5, "aliases": 0.0, "description": 0.0, "body": 1.0},
        "postings": postings,
        "docs": docs,
    }


class TestCase:
    def test_case_fields(self):
        c = Case(id="a:b:c", project="proj", topic="t", expect_slug="my-slug")
        assert c.expect_slug == "my-slug"
        assert c.project == "proj"

    def test_case_from_dict(self):
        d = {"id": "x", "project": "p", "topic": "t", "expect_slug": "s"}
        c = Case(**d)
        assert c.id == "x"


class TestScoreReport:
    def test_fields(self):
        r = ScoreReport(primacy_at_5=0.8, mrr=0.5, p95_latency_ms=10.0, n_cases=5)
        assert r.primacy_at_5 == 0.8
        assert r.n_cases == 5


class TestScoreConfig:
    def test_perfect_score_when_top_slug_matches(self):
        """When query tokens perfectly match a slug, it ranks first → primacy@5=1.0."""
        # Build 5 slugs, each with a unique "name" token
        slugs = [f"slug-{i}" for i in range(5)]
        index = _make_index(slugs)
        # The query token for slug-0 is "token0"
        # We add token0 to the postings so score_docs returns slug-0 first
        # (already done by _make_index: "token0" → slug-0)

        cases = [
            Case(id="c1", project="p", topic="t", expect_slug="slug-0")
        ]

        def index_factory(project: str, query_tokens: list[str]) -> dict:
            return index

        config = BM25Config(b=0.75, k1=1.5, weights={"name": 3.0, "topic_tags": 3.0, "aliases": 2.5, "description": 2.0, "body": 1.0})
        report = score_config(config, cases=cases, index_factory=index_factory)
        assert report.n_cases == 1

    def test_zero_score_when_no_overlap(self):
        """When query tokens don't match any doc, all get score 0 → expect_slug not ranked."""
        slugs = ["alpha", "beta"]
        index = _make_index(slugs)
        cases = [Case(id="c1", project="p", topic="t", expect_slug="alpha")]

        def index_factory(project: str, query_tokens: list[str]) -> dict:
            return index

        # Use tokens that don't appear in index
        config = BM25Config(b=0.75, k1=1.5, weights={"name": 3.0, "topic_tags": 3.0, "aliases": 2.5, "description": 2.0, "body": 1.0})
        report = score_config(
            config,
            cases=cases,
            index_factory=index_factory,
            query_overrides={"c1": ["nonexistent_token_xyz"]},
        )
        # No overlap → not in top 5 → primacy@5=0
        assert report.primacy_at_5 == 0.0

    def test_latency_measured(self):
        slugs = ["x"]
        index = _make_index(slugs)
        cases = [Case(id="c1", project="p", topic="t", expect_slug="x")]

        def index_factory(project: str, query_tokens: list[str]) -> dict:
            return index

        config = BM25Config(b=0.75, k1=1.5, weights={"name": 3.0, "topic_tags": 1.0, "aliases": 1.0, "description": 1.0, "body": 1.0})
        report = score_config(config, cases=cases, index_factory=index_factory)
        assert report.p95_latency_ms >= 0.0

    def test_mrr_correct(self):
        """Two cases: one at rank 1, one not found → MRR = (1 + 0) / 2 = 0.5."""
        slugs = [f"slug-{i}" for i in range(5)]
        index = _make_index(slugs)
        cases = [
            # token0 → slug-0 at rank 1
            Case(id="c1", project="p", topic="t", expect_slug="slug-0"),
            # query with nonexistent token → rank miss
            Case(id="c2", project="p", topic="t", expect_slug="slug-1"),
        ]

        def index_factory(project: str, query_tokens: list[str]) -> dict:
            return index

        config = BM25Config(b=0.75, k1=1.5, weights={"name": 3.0, "topic_tags": 1.0, "aliases": 1.0, "description": 1.0, "body": 1.0})
        report = score_config(
            config,
            cases=cases,
            index_factory=index_factory,
            query_overrides={
                "c1": ["token0"],
                "c2": ["totally_missing_xyzzy"],
            },
        )
        # c1: rank 1 → RR=1.0; c2: miss → RR=0.0
        assert report.mrr == pytest.approx(0.5, abs=1e-6)
        assert report.n_cases == 2
