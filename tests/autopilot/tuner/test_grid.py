"""Tests for latin hypercube sampler (_grid.py) — T1."""
from __future__ import annotations

import random

import pytest

from mnemo.autopilot.tuner._grid import (
    SearchSpace,
    BM25SearchSpace,
    latin_hypercube,
)


def _rng(seed: int = 42) -> random.Random:
    return random.Random(seed)


class TestSearchSpace:
    def test_bm25_search_space_dimensions(self):
        space = BM25SearchSpace()
        assert "b" in space.dimensions
        assert "k1" in space.dimensions
        assert "name_w" in space.dimensions
        assert "topic_w" in space.dimensions
        assert "body_w" in space.dimensions

    def test_bm25_search_space_values(self):
        space = BM25SearchSpace()
        assert space.dimensions["b"] == [0.5, 0.65, 0.75, 0.85, 0.95]
        assert space.dimensions["k1"] == [0.8, 1.2, 1.5, 1.8, 2.2]
        assert space.dimensions["name_w"] == [1, 2, 3, 5]
        assert space.dimensions["topic_w"] == [1, 2, 3]
        assert space.dimensions["body_w"] == [1, 2]

    def test_search_space_total(self):
        space = BM25SearchSpace()
        total = 1
        for vals in space.dimensions.values():
            total *= len(vals)
        assert total == 600  # 5*5*4*3*2


class TestLatinHypercube:
    def test_returns_n_samples(self):
        space = BM25SearchSpace()
        samples = latin_hypercube(space, 10, rng=_rng(42))
        assert len(samples) == 10

    def test_each_sample_has_all_dimensions(self):
        space = BM25SearchSpace()
        samples = latin_hypercube(space, 5, rng=_rng(42))
        for s in samples:
            assert set(s.keys()) == set(space.dimensions.keys())

    def test_values_from_search_space(self):
        space = BM25SearchSpace()
        samples = latin_hypercube(space, 20, rng=_rng(42))
        for s in samples:
            for dim, val in s.items():
                assert val in space.dimensions[dim], (
                    f"value {val} not in {space.dimensions[dim]} for dim {dim}"
                )

    def test_deterministic_with_same_seed(self):
        space = BM25SearchSpace()
        s1 = latin_hypercube(space, 10, rng=_rng(42))
        s2 = latin_hypercube(space, 10, rng=_rng(42))
        assert s1 == s2

    def test_different_with_different_seed(self):
        space = BM25SearchSpace()
        s1 = latin_hypercube(space, 10, rng=_rng(42))
        s2 = latin_hypercube(space, 10, rng=_rng(99))
        # at least one should differ (probabilistically certain)
        assert s1 != s2

    def test_n_zero_returns_empty(self):
        space = BM25SearchSpace()
        samples = latin_hypercube(space, 0, rng=_rng(42))
        assert samples == []

    def test_n_exceeds_space_still_works(self):
        # Should not crash even if n > total configs
        space = BM25SearchSpace()
        samples = latin_hypercube(space, 700, rng=_rng(42))
        assert len(samples) == 700
