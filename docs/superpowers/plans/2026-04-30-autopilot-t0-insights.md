# Autopilot Tier 0 — Insights — TDD Implementation Plan

**Date:** 2026-04-30
**Branch:** feat/autopilot-t0-insights
**Spec:** docs/superpowers/specs/2026-04-30-autopilot-tier0-insights-design.md

## Overview

Two features: weekly health digest + miss-collector. Pure read-only observer tier.
Lives in `src/mnemo/autopilot/insights/`. CLI extended via existing `autopilot.py`.

## Tasks

### Task 1 — `_formatters.py`: number-formatting helpers
**File:** `src/mnemo/autopilot/insights/_formatters.py`
**Test:** `tests/autopilot/insights/test_formatters.py`
- `fmt_pct(val: float) -> str` → "90.0%"
- `fmt_delta_pp(delta: float) -> str` → "Δ +0.0pp" / "Δ -1.2pp"
- `fmt_delta(delta: float) -> str` → "Δ +0.001" / "Δ -0.002"
- `fmt_int(n: int) -> str` → "1,346"
- Tests: positive delta, negative delta, zero, large int

### Task 2 — log readers: `mcp-access-log.jsonl` parser
**File:** `src/mnemo/autopilot/insights/_log_readers.py`
**Test:** `tests/autopilot/insights/test_log_readers.py`
- `read_mcp_access_log(vault_root, since_dt) -> list[dict]` — filter by timestamp >= since_dt
- `read_reflex_log(vault_root, since_dt) -> list[dict]` — parse reflex-log.jsonl
- `read_denial_log(vault_root, since_dt) -> list[dict]` — parse denial-log.jsonl
- Tests: missing file → [], malformed lines skipped, since_dt filter works

### Task 3 — recall stats parser
**File:** `src/mnemo/autopilot/insights/_log_readers.py` (extend)
**Test:** `tests/autopilot/insights/test_log_readers.py` (extend)
- `read_recall_report(vault_root) -> dict | None` — load recall-report.json
- Tests: missing file → None, valid file returns parsed dict

### Task 4 — `DigestData` dataclass + `generate_digest()`
**File:** `src/mnemo/autopilot/insights/digest.py`
**Test:** `tests/autopilot/insights/test_digest.py`
- `DigestData` dataclass with recall/reflex/denials/flags sections
- `generate_digest(vault_root, since_days=7) -> DigestData`
  - recall section from recall-report.json
  - reflex section: prompt count, emit-rate, top silence reasons, index_missing count
  - denials section: total count, top blocker rule
  - health flags from doctor output (dead rules, source path issues, etc.)
  - top emitted rules from mcp-access-log.jsonl (read_mnemo_rule calls)
- Tests with synthetic logs: assert correct counts, emit-rate, top blocker

### Task 5 — `render_digest_markdown()` + `write_digest()`
**File:** `src/mnemo/autopilot/insights/digest.py` (extend)
**Test:** `tests/autopilot/insights/test_digest.py` (extend)
- `render_digest_markdown(digest: DigestData, date_str: str) -> str`
  - Produces markdown with all spec sections
- `write_digest(vault_root, digest) -> Path`
  - Writes to `<vault>/briefings/autopilot/<YYYY-MM-DD>-digest.md`
  - Creates directory if missing
- Tests: markdown contains expected sections + numbers; file is written at correct path

### Task 6 — `post_digest_issue()`
**File:** `src/mnemo/autopilot/insights/digest.py` (extend)
**Test:** `tests/autopilot/insights/test_digest.py` (extend)
- `post_digest_issue(digest: DigestData) -> int | None`
  - Calls `gh issue create --label mnemo:digest` via subprocess
  - Returns issue number if created, None if gh missing or dry-run
  - Subprocess must be mockable via parameter injection
- Tests: mock subprocess succeeds → returns int; missing gh → None; error → None

### Task 7 — `collect_recall_misses()` miss collector
**File:** `src/mnemo/autopilot/insights/miss_collector.py`
**Test:** `tests/autopilot/insights/test_miss_collector.py`
- `collect_recall_misses(vault_root) -> int`
  - Load recall-report.json
  - For each miss in report.results where hit==False, write a `rule_candidate` proposal
  - Payload: expected_slug, topic, reason (f"miss in recall — ranked {rank}/{result_count}"), recall_report_at
  - Returns count of NEW proposals written (idempotent: skip if same expected_slug+project already pending)
- Tests:
  - No recall-report.json → returns 0
  - 2 misses → 2 proposals written
  - Second run → 0 new (idempotency)
  - Only miss results (hit==True skipped) count
  - Proposal payload fields are correct

### Task 8 — `tests/autopilot/insights/__init__.py` + conftest
**File:** `tests/autopilot/insights/__init__.py`
**Action:** Create empty init + ensure parent has init if needed

### Task 9 — CLI: `mnemo autopilot digest`
**File:** `src/mnemo/cli/commands/autopilot.py` (extend)
**File:** `src/mnemo/cli/parser.py` (extend autosub)
**Test:** `tests/autopilot/insights/test_digest_cli.py`
- Add `digest` subaction handler `_do_digest(args)`
  - Calls `generate_digest()` + `write_digest()`
  - Prints path to stdout
  - `--post` flag: also calls `post_digest_issue()`; prints issue number if got one
  - `--since` flag: parse "7d", "30d" to since_days int (default 7)
- Tests:
  - `mnemo autopilot digest` → rc=0, output contains file path
  - `--since 30d` → rc=0
  - `--post` with mocked issue creator → output contains "issue"

### Task 10 — CLI: `mnemo autopilot collect-misses`
**File:** `src/mnemo/cli/commands/autopilot.py` (extend)
**File:** `src/mnemo/cli/parser.py` (extend autosub)
**Test:** `tests/autopilot/insights/test_digest_cli.py` (extend)
- Add `collect-misses` subaction handler `_do_collect_misses(args)`
  - Calls `collect_recall_misses()`
  - Prints count of new proposals
- Tests:
  - `mnemo autopilot collect-misses` with no recall-report → rc=0, prints "0 new"
  - With synthetic misses → rc=0, prints count

### Task 11 — Cron registration in `_do_on()`
**File:** `src/mnemo/cli/commands/autopilot.py` (extend `_do_on`)
**Test:** `tests/autopilot/cli/test_autopilot_cli.py` (extend)
- On `mnemo autopilot on`, also register two scheduled jobs:
  - `autopilot.tier0.digest` cron `0 9 * * 1` → `mnemo autopilot digest --post`
  - `autopilot.tier0.collect-misses` cron `0 8 * * *` → `mnemo autopilot collect-misses`
- Tests:
  - After `autopilot on`, jobs file contains both job names with correct cron strings

### Task 12 — Full suite green + smoke
**Action:** Run `PYTHONPATH=$(pwd)/src pytest -q` — all tests pass
**Smoke:** `mnemo autopilot digest` + `mnemo autopilot collect-misses` each print output, exit 0
