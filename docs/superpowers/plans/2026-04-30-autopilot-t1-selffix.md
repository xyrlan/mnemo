# Autopilot Tier 1 — Self-Fix — TDD Task List

**Date:** 2026-04-30
**Branch:** feat/autopilot-t1-selffix
**Spec:** docs/superpowers/specs/2026-04-30-autopilot-tier1-selffix-design.md

## Tasks

### T1 — `_perimeter.py`: guard module + tests

- Write `tests/autopilot/selffix/test_perimeter.py` — assert `assert_perimeter` raises on out-of-bound path, passes on allowed paths.
- Implement `src/mnemo/autopilot/selffix/_perimeter.py` — `ALLOWED_PATHS` set + `assert_perimeter(diff: list[Path]) -> None`.

### T2 — `_gh.py`: thin gh CLI wrapper + tests

- Write `tests/autopilot/selffix/test_gh.py` — mock subprocess; verify branch create, push, pr-create return values; verify `FileNotFoundError` returns None.
- Implement `src/mnemo/autopilot/selffix/_gh.py` — `create_branch`, `push_branch`, `open_pr` (all wrap gh, return None on OSError).

### T3 — `doctor_fixer.py` data types + `detect_fixable` + tests

- Write `tests/autopilot/selffix/test_doctor_fixer.py` — fixture vault with a rule that has a dead source path; assert `detect_fixable` returns a `DoctorWarning` with kind `source_path_missing`.
- Implement `DoctorWarning` dataclass + `detect_fixable(*, vault_root) -> list[DoctorWarning]` that shells out to `mnemo doctor --json` (or imports doctor checks directly) and filters to auto-fixable categories.

### T4 — `doctor_fixer.py`: `fix_warning` for `source_path_missing` + tests

- Test: call `fix_warning` on a fixture warning; assert the source line is stripped from frontmatter; assert returned path is within perimeter.
- Implement `fix_warning(warning: DoctorWarning, *, vault_root: Path) -> Path` — strips orphan source line from frontmatter.

### T5 — `doctor_fixer.py`: `fix_warning` for `frontmatter_malformed` + tests

- Test: fixture rule with bad enforce.tool value; assert `fix_warning` replaces it; assert perimeter not violated.
- Implement regex-based frontmatter field correction.

### T6 — `doctor_fixer.py`: `open_doctor_fix_pr` + tests

- Test: mock `_gh`, `pr_budget.can_open`, `pr_budget.record_opened`; assert PR opened when budget allows; assert skipped when budget exhausted; assert perimeter called on diff.
- Implement `open_doctor_fix_pr(warnings, *, vault_root, repo_root, dry_run=False) -> int | None`.

### T7 — `dead_rule_sweep.py` data types + `detect_dead_rules` + tests

- Write `tests/autopilot/selffix/test_dead_rule_sweep.py` — synth vault with rule files + access-log fixtures (zero-hit rule vs. active rule); assert `detect_dead_rules` returns only the dead one.
- Implement `DeadRule` dataclass + `detect_dead_rules(*, vault_root, days=90) -> list[DeadRule]`.

### T8 — `dead_rule_sweep.py`: `archive_rule` + tests

- Test: assert `archive_rule` moves file to `shared/_archive/`; assert returned path is within perimeter.
- Implement `archive_rule(rule_path: Path, *, vault_root: Path) -> Path`.

### T9 — `dead_rule_sweep.py`: `open_dead_rule_pr` + tests

- Test: mock gh + budget; assert PR opened with correct branch name; assert dry_run skips PR open.
- Implement `open_dead_rule_pr(rules, *, vault_root, repo_root, dry_run=False) -> int | None`.

### T10 — `telemetry_doctor.py` data types + `scan_telemetry` + tests

- Write `tests/autopilot/selffix/test_telemetry_doctor.py` — fixture access-log with all-zero cost_usd; assert `scan_telemetry` returns `TelemetryAnomaly` with kind `cost_usd_always_zero`.
- Implement `TelemetryAnomaly` dataclass + `scan_telemetry(*, vault_root) -> list[TelemetryAnomaly]`.

### T11 — `telemetry_doctor.py`: `open_telemetry_fix_pr` + tests

- Test: mock gh + budget; assert PR opens as draft; assert dry_run skips.
- Implement `open_telemetry_fix_pr(anomalies, *, vault_root, repo_root, dry_run=False) -> int | None`.

### T12 — `outcome_poller.py`: poll_outcomes + tests

- Write `tests/autopilot/selffix/test_outcome_polling.py` — mock `gh pr list`; assert `record_outcome` called for each closed PR.
- Implement `poll_outcomes(*, vault_root) -> int` — calls `gh pr list --label mnemo:self-fix --state closed --json number,state` and feeds `pr_budget.record_outcome`.

### T13 — CLI: parser extension for `autopilot self-fix {doctor,sweep,telemetry} [--dry-run]`

- Write `tests/autopilot/selffix/test_selffix_cli.py` — `--dry-run` round-trips for each subcommand: no PR opened, list of fixable items printed.
- Extend `src/mnemo/cli/parser.py` to add `self-fix` sub-subparser under `autopilot`.
- Implement `src/mnemo/cli/commands/selffix.py` with `cmd_selffix`.

### T14 — Dispatcher: register scheduled jobs + tests

- Test: after `autopilot on`, assert three self-fix jobs recorded in autopilot-jobs.json.
- Extend `cmd_autopilot on` handler to call `schedule_autopilot_job` for T1 tier jobs.

### T15 — Integration smoke + full suite verification

- Run `PYTHONPATH=$(pwd)/src pytest -q` — must be 100% green.
- Run `mnemo autopilot self-fix doctor --dry-run` — must list fixable warnings, no PR opened.
- Verify perimeter tested explicitly in T1.
- Commit final state.

## Notes

- All Python files: `from __future__ import annotations`; `pathlib.Path` not strings; `json.dumps(..., indent=2, sort_keys=True)`.
- `_gh.py` calls: wrap in `try/except (FileNotFoundError, OSError)`.
- Python 3.8 compat: no bare `list[X]` without `from __future__ import annotations`.
- PRs always opened as draft or with label; never auto-merged.
- `pr_budget.can_open` gate at start of every PR-opening function.
