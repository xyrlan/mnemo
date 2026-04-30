# Autopilot Tier 1 — Self-Fix — Design Spec

**Date:** 2026-04-30
**Status:** Draft for review
**Depends on:** `autopilot-core` (PR #70), `autopilot-tier0-insights` (T0 ships proposals consumed here)

## Why

Tier 1 opens auto-PRs inside a strict perimeter (`shared/`, `.mnemo/`, `docs/`, `briefings/`) for fully reversible cleanup. Three concrete scopes, each with high real-world dor right now:

- **Doctor self-fix** — 15 doctor warnings live in the repo today (2 frontmatter malformed + 13 source-paths órfãos). Each fixable mechanically.
- **Dead-rule sweep** — monthly: rules with 0 hits/0 enforcement/0 reflex in 90 days → moved to `shared/_archive/`. 244 rules are "one-adoption-from-promotion" → many likely dead.
- **Telemetry bug fix** — when `mnemo doctor --check-telemetry` detects an always-null/always-zero field (e.g. `llm.call.cost_usd`), open a PR draft.

All actions go through `pr_budget.can_open` + `kill_switch.is_active` gates from core.

## Components

### 1. `autopilot/selffix/doctor_fixer.py`

```
detect_fixable() -> list[DoctorWarning]
fix_warning(warning: DoctorWarning) -> Path   # returns modified file path
open_doctor_fix_pr(warnings: list[DoctorWarning]) -> int   # returns PR number
```

Categories of warnings auto-fixable:
- **Frontmatter malformed** (e.g. `enforce.tool='Edit' should be 'Bash'`) — regex-based fix
- **Source path does not resolve** — strip the line from frontmatter (briefing was deleted)
- **Auto-promoted enforce stripped** — restore enforce block from origin if origin has it

Categories explicitly NOT auto-fixed (require human judgement):
- "rule has no auto-testable path_globs (manual verification required)"
- "Universal promotion health" status

Each PR opened:
- Branch: `mnemo/self-fix/doctor-<utc-date>`
- Label: `mnemo:self-fix`
- Body: list of warnings fixed + diff summary
- Records via `pr_budget.record_opened(category="doctor_self_fix", pr_number=N)`

### 2. `autopilot/selffix/dead_rule_sweep.py`

```
detect_dead_rules(*, vault_root, days=90) -> list[DeadRule]
archive_rule(rule_path: Path) -> Path  # moves to shared/_archive/<original-name>
open_dead_rule_pr(rules: list[DeadRule]) -> int
```

Heuristic for "dead":
- 0 hits in `mcp-access-log.jsonl` over `days`
- 0 entries in `denial-log.jsonl` over `days`
- 0 entries in `reflex-log.jsonl` (in `emitted` arrays) over `days`
- 0 enforcement triggers (PreToolUse hook log)
- Created at least `days` ago

PR opens at most monthly (`pr_budget` daily cap = 1 ⇒ effectively at most 30 in 30 days, but plus dispatcher cron set to monthly).

### 3. `autopilot/selffix/telemetry_doctor.py`

```
scan_telemetry(*, vault_root) -> list[TelemetryAnomaly]
open_telemetry_fix_pr(anomalies) -> int
```

Anomalies surfaced today by raw inspection:
- `cost_usd` field on `llm.call` is always 0 → pricing table not applied
- `prompt_tokens` field is null on early reflex entries (76/1346)

PR opens as **draft** (not auto-merged); body explains what's broken so a human can fix the root cause.

### 4. CLI extensions

```
mnemo autopilot self-fix doctor      # one-shot: detect + open PR
mnemo autopilot self-fix sweep       # one-shot: dead rule sweep
mnemo autopilot self-fix telemetry   # one-shot: telemetry anomaly draft
mnemo autopilot self-fix --dry-run   # all of the above, but no PR opened
```

### 5. Scheduled jobs

Registered on `mnemo autopilot on`:
- `autopilot.tier1.doctor` — `0 10 * * 1` (weekly Monday 10:00 UTC)
- `autopilot.tier1.sweep` — `0 11 1 * *` (monthly 1st 11:00 UTC)
- `autopilot.tier1.telemetry` — `0 12 * * 0` (weekly Sunday 12:00 UTC)

## PR opening flow

```
1. detect_*() returns N warnings
2. for each category: pr_budget.can_open(category)
3. if blocked, log + skip (no error)
4. otherwise:
   a. checkout fresh branch from master HEAD
   b. apply fixes in worktree
   c. run pytest (must pass — abort PR otherwise)
   d. push + open PR via gh
   e. pr_budget.record_opened(category, pr_number)
   f. write proposal status="applied", applied_pr=N
5. on PR close/merge (via gh webhook? or polling?):
   - pr_budget.record_outcome(pr_number, outcome)
```

**Polling for outcomes:** instead of webhooks, a daily job `autopilot.tier1.poll-outcomes` runs `gh pr list --label mnemo:self-fix --state closed` and feeds `record_outcome`.

## Perimeter enforcement

A guard in `selffix/_perimeter.py`:

```python
ALLOWED_PATHS = {"shared/", ".mnemo/", "docs/", "briefings/", "src/mnemo/autopilot/_archive/"}

def assert_perimeter(diff: list[Path]) -> None:
    """Raise if any path outside ALLOWED_PATHS is touched."""
```

Called before every PR open. Refusal aborts the PR.

## Tests

Coverage targets:
- `test_doctor_fixer.py` — fix each warning category in fixture vault, assert exact diff
- `test_dead_rule_sweep.py` — synth log fixtures, assert detection criteria
- `test_telemetry_doctor.py` — fixture access-log with cost_usd=0, assert anomaly
- `test_perimeter.py` — assert refusal on out-of-bound paths
- `test_selffix_cli.py` — `--dry-run` round-trips
- `test_outcome_polling.py` — gh mock + `record_outcome` calls

## File structure

```
src/mnemo/autopilot/selffix/
├── __init__.py
├── doctor_fixer.py
├── dead_rule_sweep.py
├── telemetry_doctor.py
├── outcome_poller.py
├── _perimeter.py
└── _gh.py             # thin wrapper around gh CLI for branch/push/pr-create
```

Estimated: ~700 LOC prod, ~900 LOC tests.

## Risks

- **PR explosion.** `pr_budget` cap=1/category/day prevents this.
- **Bad fix on doctor warning.** Each PR runs `pytest` before pushing; merge always manual.
- **`gh` unavailable.** `_gh.py` returns `None`/skips; user sees "skipped (no gh)".

## Out of scope

- Auto-merge of self-fix PRs (always human review).
- Cross-repo self-fix (single repo only).

## Spec self-review

- ✅ No TBD
- ✅ Perimeter enforced via `assert_perimeter`
- ✅ Each PR category gated by `pr_budget`
- ✅ Test fixtures listed
- ✅ Scope: 700 LOC fits one plan
