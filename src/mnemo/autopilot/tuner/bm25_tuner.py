"""BM25F grid search tuner.

Loads the frozen recall set, runs a latin-hypercube grid search over
BM25F hyperparameters, and proposes the best config if it meets the
acceptance criteria.

Never modifies production code paths — only writes bm25-config.json
to the repo root (or wherever `config_path` points).
"""
from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# BM25Config dataclass
# ---------------------------------------------------------------------------

@dataclass
class BM25Config:
    b: float
    k1: float
    weights: dict[str, float]

    def to_dict(self) -> dict:
        return {
            "b": self.b,
            "k1": self.k1,
            "weights": dict(self.weights),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BM25Config":
        return cls(
            b=float(d["b"]),
            k1=float(d["k1"]),
            weights={k: float(v) for k, v in d["weights"].items()},
        )


DEFAULT_BM25_CONFIG = BM25Config(
    b=0.75,
    k1=1.5,
    weights={
        "name": 3.0,
        "topic_tags": 3.0,
        "aliases": 2.5,
        "description": 2.0,
        "body": 1.0,
    },
)


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

def write_bm25_config(config: BM25Config, path: Path) -> None:
    """Atomically write BM25Config to path as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(config.to_dict(), indent=2, sort_keys=True))
        os.replace(tmp, path)
    except OSError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def load_bm25_config(path: Path) -> Optional[BM25Config]:
    """Load BM25Config from path. Returns None if missing or invalid."""
    try:
        data = json.loads(path.read_text())
        return BM25Config.from_dict(data)
    except (FileNotFoundError, KeyError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Acceptance gates
# ---------------------------------------------------------------------------

def meets_acceptance(before: "ScoreReport", after: "ScoreReport") -> bool:  # type: ignore[name-defined]
    """Return True only if ALL acceptance criteria are satisfied.

    Criteria:
    - primacy@5 increases by >= 2pp (0.02)
    - MRR increases by >= 0.02
    - p95 latency does NOT regress by more than 5%
    """
    primacy_ok = (after.primacy_at_5 - before.primacy_at_5) >= 0.02
    mrr_ok = (after.mrr - before.mrr) >= 0.02
    # Latency: if before is 0, any after value is fine
    if before.p95_latency_ms == 0.0:
        latency_ok = True
    else:
        latency_ok = after.p95_latency_ms <= before.p95_latency_ms * 1.05
    return primacy_ok and mrr_ok and latency_ok


# ---------------------------------------------------------------------------
# Grid search
# ---------------------------------------------------------------------------

def _sample_to_config(sample: dict) -> BM25Config:
    return BM25Config(
        b=float(sample["b"]),
        k1=float(sample["k1"]),
        weights={
            "name": float(sample["name_w"]),
            "topic_tags": float(sample.get("topic_w", 3)),
            "aliases": 2.5,  # not tuned in this grid
            "description": 2.0,  # not tuned in this grid
            "body": float(sample["body_w"]),
        },
    )


def grid_search(
    *,
    vault_root: Path,
    max_iterations: int = 200,
    rng_seed: int = 42,
    config_path: Optional[Path] = None,
    index_factory: Optional[Callable[[str, list[str]], dict]] = None,
) -> Optional[BM25Config]:
    """Run latin-hypercube grid search over BM25F hyperparameters.

    Returns the best BM25Config found, or None if:
    - The frozen recall set is missing.
    - No config improves on the baseline.

    Args:
        vault_root: Path to the vault root (contains .mnemo/).
        max_iterations: Maximum number of configs to evaluate.
        rng_seed: Seed for deterministic sampling.
        config_path: Optional override for bm25-config.json path.
        index_factory: Optional callable for test injection.
            Signature: (project: str, query_tokens: list[str]) -> index dict.
    """
    import json as _json
    from mnemo.autopilot.core.frozen_recall import load_frozen, FrozenSetMissing
    from mnemo.autopilot.tuner._grid import BM25SearchSpace, latin_hypercube
    from mnemo.autopilot.tuner._scorer import Case, score_config

    # Load frozen set
    try:
        fh = load_frozen(vault_root=vault_root)
    except FrozenSetMissing:
        return None

    with fh:
        raw_cases = _json.load(fh)

    # raw_cases may be a list directly or a dict with a "cases" key
    if isinstance(raw_cases, list):
        cases_data = raw_cases
    elif isinstance(raw_cases, dict):
        cases_data = raw_cases.get("cases", [])
    else:
        return None

    cases = [
        Case(
            id=c.get("id", ""),
            project=c.get("project", ""),
            topic=c.get("topic", ""),
            expect_slug=c.get("expect_slug", ""),
        )
        for c in cases_data
        if isinstance(c, dict) and c.get("expect_slug")
    ]
    if not cases:
        return None

    # Build default index_factory if not injected
    if index_factory is None:
        index_factory = _build_default_index_factory(vault_root)

    # Baseline
    baseline = score_config(DEFAULT_BM25_CONFIG, cases=cases, index_factory=index_factory)

    # Grid search
    space = BM25SearchSpace()
    rng = random.Random(rng_seed)
    samples = latin_hypercube(space, max_iterations, rng=rng)

    best_config: Optional[BM25Config] = None
    best_after = baseline

    for sample in samples:
        config = _sample_to_config(sample)
        report = score_config(config, cases=cases, index_factory=index_factory)
        # Track the config that maximally improves the weighted objective
        # Simple objective: primacy_at_5 + mrr (equal weight)
        if (report.primacy_at_5 + report.mrr) > (best_after.primacy_at_5 + best_after.mrr):
            best_config = config
            best_after = report

    if best_config is None:
        return None

    # Only return if it meets acceptance criteria
    if meets_acceptance(baseline, best_after):
        return best_config
    return None


def _build_default_index_factory(vault_root: Path) -> Callable[[str, list[str]], dict]:
    """Build a real index factory using the vault's reflex index."""
    def factory(project: str, query_tokens: list[str]) -> dict:
        try:
            from mnemo.core.reflex.index import load_index
            idx = load_index(vault_root)
            if idx is None:
                return {"doc_count": 0, "docs": {}, "postings": {}, "avg_field_length": {}}
            return idx
        except Exception:
            return {"doc_count": 0, "docs": {}, "postings": {}, "avg_field_length": {}}
    return factory


# ---------------------------------------------------------------------------
# PR opening
# ---------------------------------------------------------------------------

def open_bm25_tune_pr(
    config: BM25Config,
    before: "ScoreReport",  # type: ignore[name-defined]
    after: "ScoreReport",  # type: ignore[name-defined]
    *,
    vault_root: Path,
    dry_run: bool = False,
    config_path: Optional[Path] = None,
) -> int:
    """Propose or apply the tuned BM25 config.

    Returns:
        -2  — skipped (kill switch off or budget exhausted)
        -1  — dry run (printed proposal, no writes)
         0  — config written successfully
    """
    delta_p = after.primacy_at_5 - before.primacy_at_5
    delta_m = after.mrr - before.mrr
    delta_l = after.p95_latency_ms - before.p95_latency_ms

    proposal_text = (
        f"[bm25-tuner] Proposed BM25F config:\n"
        f"  b={config.b}, k1={config.k1}\n"
        f"  weights={config.weights}\n"
        f"  primacy@5: {before.primacy_at_5:.4f} → {after.primacy_at_5:.4f} (Δ{delta_p:+.4f})\n"
        f"  MRR:       {before.mrr:.4f} → {after.mrr:.4f} (Δ{delta_m:+.4f})\n"
        f"  p95 (ms):  {before.p95_latency_ms:.2f} → {after.p95_latency_ms:.2f} (Δ{delta_l:+.2f})\n"
    )

    if dry_run:
        print(f"[dry-run] {proposal_text}")
        return -1

    # Gate on kill switch + budget
    from mnemo.autopilot.core.pr_budget import can_open, record_opened
    ok, reason = can_open(vault_root=vault_root, category="bm25_tune")
    if not ok:
        print(f"[bm25-tuner] Skipping: {reason}")
        return -2

    # Write config — default to vault_root / bm25-config.json
    target = config_path or (vault_root / "bm25-config.json")
    write_bm25_config(config, target)
    record_opened(vault_root=vault_root, category="bm25_tune", pr_number=0)
    print(f"[bm25-tuner] Config written to {target}")
    print(proposal_text)
    return 0
