# Autopilot Tier 2 — Self-Tuner TDD Plan

**Date:** 2026-04-30
**Spec:** `docs/superpowers/specs/2026-04-30-autopilot-tier2-tuner-design.md`
**Branch:** `feat/autopilot-t2-tuner`

## Overview

Two components:
1. **BM25F grid search** (`bm25_tuner.py`) — latin hypercube over 600 configs, propose best
2. **Reflex per-project calibrator** (`reflex_calibrator.py`) — analyse `reflex-log.jsonl`, propose thresholds

Both live in `src/mnemo/autopilot/tuner/`. Supporting modules: `_grid.py` (LHS sampler) and `_scorer.py` (frozen-set evaluator).

New CLI subcommand: `mnemo autopilot tune {bm25,reflex,all} [--dry-run]`

## Tasks

### T1 — Latin hypercube sampler (`_grid.py`) [TDD]
Write `tests/autopilot/tuner/test_grid.py` first:
- Deterministic output with seeded `random.Random(42)`
- Returns exactly N samples from the joint search space
- Each sample covers the full set of dimensions
- No numpy dependency (stdlib only)

Implement `src/mnemo/autopilot/tuner/_grid.py`:
- `SearchSpace` dataclass holding dimension lists
- `BM25SearchSpace` — the 5×5×4×3×2 space for (b, k1, name_w, topic_w, body_w)
- `latin_hypercube(space, n, *, rng)` → `list[dict]` — stratified random samples

### T2 — Frozen-set evaluator (`_scorer.py`) [TDD]
Write `tests/autopilot/tuner/test_scorer.py`:
- Synthetic frozen set (10 cases, fixed queries, known expected slugs)
- `score_config` returns `ScoreReport(primacy_at_5, mrr, p95_latency_ms)`
- p95 latency is measured real-time (time.perf_counter)
- Returns baseline zeros when frozen set missing (FrozenSetMissing handled)

Implement `src/mnemo/autopilot/tuner/_scorer.py`:
- `Case` dataclass: `id, project, topic, expect_slug`
- `ScoreReport` dataclass: `primacy_at_5, mrr, p95_latency_ms, n_cases`
- `score_config(config, *, cases, index_factory)` — runs each case, returns report
- `primacy_at_5`: fraction of cases where expect_slug appears in top-5 results
- `mrr`: mean reciprocal rank (0 if not found in top-10)

### T3 — BM25 config dataclass + JSON I/O [TDD]
Write `tests/autopilot/tuner/test_bm25_tuner.py` (part 1):
- `BM25Config` serialises to/from dict cleanly
- `write_bm25_config` writes to path, `load_bm25_config` reads it back
- Round-trip preserves all fields

Implement in `src/mnemo/autopilot/tuner/bm25_tuner.py`:
- `BM25Config` dataclass: `b, k1, weights: dict[str,float]`
- `write_bm25_config(config, path)` — atomic JSON write
- `load_bm25_config(path)` → `BM25Config | None`
- `DEFAULT_BM25_CONFIG` matching current `reflex/bm25.py` defaults

### T4 — BM25 grid search (core logic) [TDD]
Write `tests/autopilot/tuner/test_bm25_tuner.py` (part 2):
- Seeded grid_search over synthetic frozen set returns deterministic best config
- `max_iterations=10` for speed, seed=42
- Assert best config has higher primacy@5 than baseline on synthetic data
- Returns `None` when frozen set missing

Implement `grid_search(*, vault_root, max_iterations=200, rng_seed=42)` in `bm25_tuner.py`:
- Loads frozen set via `frozen_recall.load_frozen`
- Builds baseline report using DEFAULT_BM25_CONFIG
- Samples `max_iterations` configs via `latin_hypercube`
- Scores each config; tracks best improvement
- Returns best `BM25Config` or `None` if no improvement

### T5 — BM25 acceptance gates [TDD]
Write `tests/autopilot/tuner/test_bm25_acceptance.py`:
- `meets_acceptance(before, after)` returns False when primacy delta < 2pp
- Returns False when MRR delta < 0.02
- Returns False when p95 latency regresses > 5%
- Returns True only when ALL criteria pass

Implement `meets_acceptance(before, after)` in `bm25_tuner.py`:
- Check primacy delta >= 0.02 (2pp)
- Check MRR delta >= 0.02
- Check after.p95_latency_ms <= before.p95_latency_ms * 1.05
- Return bool

### T6 — BM25 PR opening [TDD]
Write `tests/autopilot/tuner/test_bm25_tuner.py` (part 3):
- `open_bm25_tune_pr` skips when `can_open` returns False (kill switch off)
- `open_bm25_tune_pr` skips (dry_run=True) → returns -1 without writing config
- dry_run=True prints the proposed config without writing files
- Non-dry-run writes `bm25-config.json` and records budget

Implement `open_bm25_tune_pr(config, before, after, *, vault_root, dry_run=False)` in `bm25_tuner.py`:
- Gate on `pr_budget.can_open(vault_root=vault_root, category="bm25_tune")`
- dry_run=True: print proposed config JSON + score delta, return -1
- Non-dry-run: write config file, record_opened, return 0 (PR creation is out of scope)

### T7 — Reflex log parsing [TDD]
Write `tests/autopilot/tuner/test_reflex_calibrator.py` (part 1):
- `analyze_reflex_log` parses synthetic JSONL with known emit/silence counts
- 30-day window filter respected (entries older than 30d excluded)
- Per-project stats: total_prompts, emitted_count, silence_reasons breakdown
- Returns empty stats when log missing (graceful)
- Project filter: `project=None` aggregates all; `project="foo"` filters

Implement `src/mnemo/autopilot/tuner/reflex_calibrator.py`:
- `ReflexStats` dataclass: `project, total_prompts, emitted_count, silence_reasons: dict[str,int], days_covered`
- `analyze_reflex_log(*, vault_root, project=None, window_days=30)` → `dict[str, ReflexStats]`
- Parse `.mnemo/reflex-log.jsonl` line by line, skip invalid JSON
- Filter by `ts` within window_days of today

### T8 — Reflex threshold calibration [TDD]
Write `tests/autopilot/tuner/test_reflex_calibrator.py` (part 2):
- `calibrate_thresholds` returns default config when stats has < 100 prompts
- Calibration targets 5–10% emit-rate
- If current rate < 3%, lower `relative_gap` and `absolute_floor`
- If current rate > 12%, raise thresholds
- Predicted rate stays in [3%, 12%]

Implement `calibrate_thresholds(stats)` in `reflex_calibrator.py`:
- `ReflexConfig` dataclass: `project, relative_gap, absolute_floor, min_tokens`
- Return `None` if `stats.total_prompts < 100` (insufficient data)
- Current emit-rate = `emitted_count / total_prompts`
- Linear interpolation: map rate → threshold adjustment
- Clamp output to safe ranges: relative_gap ∈ [1.1, 3.0], absolute_floor ∈ [0.5, 5.0]

### T9 — Reflex config JSON I/O [TDD]
Write `tests/autopilot/tuner/test_reflex_calibrator.py` (part 3):
- Round-trip `ReflexConfig` to/from `.mnemo/reflex-config.{project}.json`
- `write_reflex_config` creates parent dirs as needed
- `load_reflex_config` returns None when file missing

Implement `write_reflex_config(config, vault_root)` and `load_reflex_config(project, vault_root)`:
- Path: `vault_root / ".mnemo" / f"reflex-config.{config.project}.json"`
- Atomic write (write to .tmp then rename)

### T10 — Reflex PR opening + acceptance [TDD]
Write `tests/autopilot/tuner/test_reflex_calibrator.py` (part 4):
- `open_reflex_calibration_pr` dry_run prints configs without writing files
- Skips project with < 100 prompts (insufficient data)
- Gated by `pr_budget.can_open(category="reflex_calibration")`
- dry_run=True returns -1

Implement `open_reflex_calibration_pr(per_project, *, vault_root, dry_run=False)`:
- Filter out None configs (insufficient data)
- Gate on `pr_budget.can_open(vault_root=vault_root, category="reflex_calibration")`
- dry_run: print each config, return -1
- Non-dry-run: write all reflex-config files, record_opened, return 0

### T11 — CLI `mnemo autopilot tune` [TDD]
Write `tests/autopilot/cli/test_tune_cli.py`:
- `mnemo autopilot tune bm25 --dry-run` exits 0 when no frozen set (graceful abort)
- `mnemo autopilot tune reflex --dry-run` exits 0 when no log (graceful abort)
- `mnemo autopilot tune all --dry-run` runs both in sequence
- `--project NAME` passed through to reflex calibrator
- Output contains "proposed" keyword in dry-run mode for bm25
- Output contains "no frozen" when frozen set missing

Implement `tune` subcommand in `src/mnemo/cli/commands/autopilot.py`:
- Add `tune` to `autopilot_action` dispatch
- Parse `tune_target` subcommand: bm25 / reflex / all
- Wire `--dry-run`, `--project` arguments
- Import and call tuner modules lazily

### T12 — Scheduled jobs registration [TDD]
Write `tests/autopilot/tuner/test_tune_scheduling.py`:
- `register_tune_jobs(vault_root)` adds two jobs to dispatcher
- Job names: `autopilot.tier2.bm25` and `autopilot.tier2.reflex`
- Crons: `0 13 * * 0` and `0 14 * * 0`
- Commands: `mnemo autopilot tune bm25` and `mnemo autopilot tune reflex`
- Idempotent: calling twice doesn't duplicate entries

Implement `register_tune_jobs(vault_root)` in `src/mnemo/autopilot/tuner/__init__.py`:
- Calls `schedule_autopilot_job` for each job
- Called from `mnemo autopilot on` handler (or lazily from tune command)

### T13 — `__init__.py` exports [TDD]
Write final integration test in `tests/autopilot/tuner/test_init.py`:
- `from mnemo.autopilot.tuner import register_tune_jobs` works
- Module imports cleanly with no side effects

Implement proper `__init__.py` with `__all__` and exported symbols.

### T14 — Full suite green + smoke check
- `PYTHONPATH=$(pwd)/src pytest -q` all pass
- `mnemo autopilot tune bm25 --dry-run` prints "no frozen recall set" gracefully
- `mnemo autopilot tune reflex --dry-run` prints "no reflex log" gracefully
- Final test count delta documented

## File Structure

```
src/mnemo/autopilot/tuner/
├── __init__.py          (register_tune_jobs)
├── _grid.py             (latin_hypercube, SearchSpace)
├── _scorer.py           (Case, ScoreReport, score_config)
├── bm25_tuner.py        (BM25Config, grid_search, open_bm25_tune_pr)
└── reflex_calibrator.py (ReflexStats, ReflexConfig, calibrate_thresholds, ...)

tests/autopilot/
├── tuner/
│   ├── __init__.py
│   ├── test_grid.py
│   ├── test_scorer.py
│   ├── test_bm25_tuner.py
│   ├── test_bm25_acceptance.py
│   ├── test_reflex_calibrator.py
│   ├── test_tune_scheduling.py
│   └── test_init.py
└── cli/
    └── test_tune_cli.py  (extend existing)
```

## Acceptance Gates

- BM25 PR: primacy@5 +≥2pp AND MRR +≥0.02 AND p95 latency ≤ +5%
- Reflex: ≥100 prompts AND predicted emit-rate ∈ [3%, 12%]
- Existing 1145 tests still pass
