# Autopilot Tier 2 — Self-Tuner — Design Spec

**Date:** 2026-04-30
**Status:** Draft for review
**Depends on:** `autopilot-core` (frozen_recall, pr_budget, dispatcher)

## Why

Two parameters in mnemo are hardcoded today and arguably overdue for data-driven calibration:

1. **BM25F field weights + b/k1** in `rule_activation/index.py`. Affect recall.
2. **Reflex thresholds** (relative_gap, absolute_floor, min_tokens) in `core/reflex/`. Affect emit-rate.

Tier 2 closes the loop: telemetry → grid search → PR with new constants. Frozen recall set (from core) prevents Goodhart drift.

## Components

### 1. `autopilot/tuner/bm25_tuner.py`

```
load_frozen_set() -> list[Case]
score_config(config: BM25Config) -> ScoreReport
grid_search(*, vault_root, max_iterations=200) -> Optional[BM25Config]
open_bm25_tune_pr(config: BM25Config, before: ScoreReport, after: ScoreReport) -> int
```

**Search space:**
- `b` ∈ {0.5, 0.65, 0.75, 0.85, 0.95}
- `k1` ∈ {0.8, 1.2, 1.5, 1.8, 2.2}
- field weight `name` ∈ {1, 2, 3, 5}
- field weight `topic` ∈ {1, 2, 3}
- field weight `body` ∈ {1, 2}

Total: 5×5×4×3×2 = 600 configs. Cap at 200 random samples (latin hypercube).

**Acceptance criteria** (must ALL hold to open PR):
- `primacy@5` increases by ≥2pp
- `MRR` increases by ≥0.02
- `p95 latency` does not regress >5%

If accepted, write `bm25-config.json` to repo + open PR.

**Frozen set guarantee:** all evaluation runs against `recall-cases.frozen.json` from core. The live set can drift; tuner never sees it.

### 2. `autopilot/tuner/reflex_calibrator.py`

```
analyze_reflex_log(*, vault_root, project=None) -> ReflexStats
calibrate_thresholds(stats: ReflexStats) -> ReflexConfig
open_reflex_calibration_pr(per_project: dict[str, ReflexConfig]) -> int
```

Per-project calibration: learn `relative_gap`, `absolute_floor`, `min_tokens` thresholds that target a 5–10% emit-rate per project (today's global is 5.6%).

Output: `reflex-config.{project}.json` files, with global default still hardcoded.

Acceptance:
- Each project has ≥100 prompts in last 30 days (otherwise insufficient data, skip)
- Predicted emit-rate ∈ [3%, 12%]
- Doesn't break existing reflex tests

### 3. CLI extensions

```
mnemo autopilot tune bm25 [--dry-run]
mnemo autopilot tune reflex [--dry-run] [--project NAME]
mnemo autopilot tune all [--dry-run]
```

### 4. Scheduled jobs

- `autopilot.tier2.bm25` — `0 13 * * 0` (weekly Sunday 13:00 UTC) → `mnemo autopilot tune bm25`
- `autopilot.tier2.reflex` — `0 14 * * 0` (weekly Sunday 14:00 UTC) → `mnemo autopilot tune reflex`

Both gated by `pr_budget` (categories `bm25_tune` and `reflex_calibration`).

## File structure

```
src/mnemo/autopilot/tuner/
├── __init__.py
├── bm25_tuner.py
├── reflex_calibrator.py
├── _grid.py        # latin hypercube sampler
└── _scorer.py      # frozen-set evaluator (delegates to mnemo.core.rule_activation)
```

Estimated: ~600 LOC prod, ~800 LOC tests.

## Tests

- `test_bm25_grid.py` — synthetic frozen set, fixed seed, assert deterministic best config
- `test_bm25_acceptance.py` — assert PR NOT opened when criteria fail
- `test_reflex_calibrator.py` — synth reflex log per project, assert thresholds
- `test_tuner_cli.py` — `--dry-run` round-trip

## Risks

- **Goodhart on frozen set.** Mitigation: frozen set is locked at `mnemo autopilot on` time; can only be refreshed by manual `mnemo autopilot freeze-recall --force`.
- **Reflex calibration too aggressive.** Mitigation: hard floor of 100 prompts per project before calibration runs.
- **bm25 PR opened against tests that hardcode old weights.** Mitigation: tuner runs `pytest tests/recall/` before opening PR.

## Out of scope

- Multi-objective optimization (single weighted score only).
- Online learning (offline batch only).
- Auto-merge (always human review).

## Spec self-review

- ✅ Frozen set is the only training data → drift-safe
- ✅ Acceptance criteria are absolute thresholds, not pure ranking
- ✅ Each PR is gated by `pr_budget`
- ✅ Scope ≤ 600 LOC fits one plan
