"""Tests for BM25 tuner (bm25_tuner.py) — T3, T4, T6."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mnemo.autopilot.tuner.bm25_tuner import (
    BM25Config,
    DEFAULT_BM25_CONFIG,
    write_bm25_config,
    load_bm25_config,
    grid_search,
    open_bm25_tune_pr,
)
from mnemo.autopilot.tuner._scorer import ScoreReport


# ---------------------------------------------------------------------------
# T3 — BM25Config dataclass + JSON I/O
# ---------------------------------------------------------------------------

class TestBM25Config:
    def test_default_config_fields(self):
        c = DEFAULT_BM25_CONFIG
        assert 0.0 < c.b <= 1.0
        assert c.k1 > 0
        assert "name" in c.weights
        assert "body" in c.weights

    def test_to_dict_round_trip(self):
        c = BM25Config(b=0.75, k1=1.5, weights={"name": 3.0, "body": 1.0})
        d = c.to_dict()
        c2 = BM25Config.from_dict(d)
        assert c2.b == c.b
        assert c2.k1 == c.k1
        assert c2.weights == c.weights

    def test_write_and_load_round_trip(self, tmp_path: Path):
        config = BM25Config(b=0.65, k1=1.2, weights={"name": 2.0, "topic_tags": 2.0, "aliases": 2.5, "description": 2.0, "body": 1.0})
        out = tmp_path / "bm25-config.json"
        write_bm25_config(config, out)
        assert out.exists()
        loaded = load_bm25_config(out)
        assert loaded is not None
        assert loaded.b == config.b
        assert loaded.k1 == config.k1
        assert loaded.weights == config.weights

    def test_load_returns_none_when_missing(self, tmp_path: Path):
        result = load_bm25_config(tmp_path / "nonexistent.json")
        assert result is None

    def test_write_is_valid_json(self, tmp_path: Path):
        config = BM25Config(b=0.75, k1=1.5, weights={"name": 3.0, "body": 1.0})
        out = tmp_path / "bm25-config.json"
        write_bm25_config(config, out)
        data = json.loads(out.read_text())
        assert "b" in data
        assert "k1" in data
        assert "weights" in data


# ---------------------------------------------------------------------------
# T4 — grid_search
# ---------------------------------------------------------------------------

def _write_frozen(vault_root: Path, cases: list[dict]) -> None:
    d = vault_root / ".mnemo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "recall-cases.frozen.json").write_text(json.dumps(cases))


class TestGridSearch:
    def test_returns_none_when_frozen_missing(self, tmp_path: Path):
        result = grid_search(vault_root=tmp_path, max_iterations=5, rng_seed=42)
        assert result is None

    def test_returns_bm25_config_or_none(self, tmp_path: Path):
        cases = [
            {"id": "c1", "project": "p", "topic": "t", "expect_slug": "slug-0"},
        ]
        _write_frozen(tmp_path, cases)

        # Patch index_factory to return a minimal index
        import mnemo.autopilot.tuner.bm25_tuner as mod
        slugs = [f"slug-{i}" for i in range(5)]
        # Build an index where token "t" → slug-0
        postings: dict = {"t": [{"slug": "slug-0", "tf": {"name": 1}}]}
        docs = {
            slug: {
                "field_length": {"name": 1, "topic_tags": 0, "aliases": 0, "description": 0, "body": 0},
                "preview": slug,
            }
            for slug in slugs
        }
        fake_index = {
            "schema_version": 1,
            "doc_count": len(slugs),
            "avg_field_length": {"name": 1.0, "topic_tags": 0.0, "aliases": 0.0, "description": 0.0, "body": 0.0},
            "postings": postings,
            "docs": docs,
        }

        def fake_factory(project: str, query_tokens: list[str]) -> dict:
            return fake_index

        result = grid_search(
            vault_root=tmp_path,
            max_iterations=10,
            rng_seed=42,
            index_factory=fake_factory,
        )
        # result may be None (no improvement) or a BM25Config; both are valid
        assert result is None or isinstance(result, BM25Config)

    def test_deterministic_with_same_seed(self, tmp_path: Path):
        cases = [{"id": "c1", "project": "p", "topic": "t", "expect_slug": "slug-0"}]
        _write_frozen(tmp_path, cases)

        fake_index = {
            "schema_version": 1,
            "doc_count": 1,
            "avg_field_length": {"name": 1.0, "topic_tags": 0.0, "aliases": 0.0, "description": 0.0, "body": 0.0},
            "postings": {"t": [{"slug": "slug-0", "tf": {"name": 1}}]},
            "docs": {"slug-0": {"field_length": {"name": 1, "topic_tags": 0, "aliases": 0, "description": 0, "body": 0}, "preview": "s"}},
        }

        def fake_factory(project: str, query_tokens: list[str]) -> dict:
            return fake_index

        r1 = grid_search(vault_root=tmp_path, max_iterations=5, rng_seed=42, index_factory=fake_factory)
        r2 = grid_search(vault_root=tmp_path, max_iterations=5, rng_seed=42, index_factory=fake_factory)
        # Results should match (both None or both same config)
        if r1 is None:
            assert r2 is None
        else:
            assert r2 is not None
            assert r1.b == r2.b
            assert r1.k1 == r2.k1


# ---------------------------------------------------------------------------
# T6 — open_bm25_tune_pr
# ---------------------------------------------------------------------------

class TestOpenBM25TunePR:
    def test_dry_run_returns_minus_one(self, tmp_path: Path, capsys):
        config = BM25Config(b=0.65, k1=1.2, weights={"name": 2.0, "topic_tags": 3.0, "aliases": 2.5, "description": 2.0, "body": 1.0})
        before = ScoreReport(primacy_at_5=0.5, mrr=0.3, p95_latency_ms=5.0, n_cases=10)
        after = ScoreReport(primacy_at_5=0.6, mrr=0.35, p95_latency_ms=4.8, n_cases=10)

        result = open_bm25_tune_pr(config, before, after, vault_root=tmp_path, dry_run=True)
        assert result == -1

    def test_dry_run_prints_proposed(self, tmp_path: Path, capsys):
        config = BM25Config(b=0.65, k1=1.2, weights={"name": 2.0, "topic_tags": 3.0, "aliases": 2.5, "description": 2.0, "body": 1.0})
        before = ScoreReport(primacy_at_5=0.5, mrr=0.3, p95_latency_ms=5.0, n_cases=10)
        after = ScoreReport(primacy_at_5=0.6, mrr=0.35, p95_latency_ms=4.8, n_cases=10)

        open_bm25_tune_pr(config, before, after, vault_root=tmp_path, dry_run=True)
        captured = capsys.readouterr()
        assert "proposed" in captured.out.lower() or "dry" in captured.out.lower() or "bm25" in captured.out.lower()

    def test_dry_run_does_not_write_config(self, tmp_path: Path):
        config = BM25Config(b=0.65, k1=1.2, weights={"name": 2.0, "topic_tags": 3.0, "aliases": 2.5, "description": 2.0, "body": 1.0})
        before = ScoreReport(primacy_at_5=0.5, mrr=0.3, p95_latency_ms=5.0, n_cases=10)
        after = ScoreReport(primacy_at_5=0.6, mrr=0.35, p95_latency_ms=4.8, n_cases=10)

        open_bm25_tune_pr(config, before, after, vault_root=tmp_path, dry_run=True)
        assert not (tmp_path / "bm25-config.json").exists()

    def test_skips_when_kill_switch_off(self, tmp_path: Path, capsys):
        """When autopilot is not active, open_bm25_tune_pr should skip."""
        config = BM25Config(b=0.65, k1=1.2, weights={"name": 2.0, "topic_tags": 3.0, "aliases": 2.5, "description": 2.0, "body": 1.0})
        before = ScoreReport(primacy_at_5=0.5, mrr=0.3, p95_latency_ms=5.0, n_cases=10)
        after = ScoreReport(primacy_at_5=0.6, mrr=0.35, p95_latency_ms=4.8, n_cases=10)

        # kill switch is off by default (no state file)
        result = open_bm25_tune_pr(config, before, after, vault_root=tmp_path, dry_run=False)
        assert result == -2  # skipped due to budget/kill switch

    def test_non_dry_run_writes_config_when_active(self, tmp_path: Path):
        """When autopilot is active and budget allows, config should be written."""
        from mnemo.autopilot.core.kill_switch import set_state
        set_state(vault_root=tmp_path, state="on")

        config = BM25Config(b=0.65, k1=1.2, weights={"name": 2.0, "topic_tags": 3.0, "aliases": 2.5, "description": 2.0, "body": 1.0})
        before = ScoreReport(primacy_at_5=0.5, mrr=0.3, p95_latency_ms=5.0, n_cases=10)
        after = ScoreReport(primacy_at_5=0.6, mrr=0.35, p95_latency_ms=4.8, n_cases=10)

        result = open_bm25_tune_pr(config, before, after, vault_root=tmp_path, dry_run=False)
        assert result == 0
        assert (tmp_path / "bm25-config.json").exists()
