# Autopilot Tier 3 — Rule Proposer — TDD Plan

**Date:** 2026-04-30
**Branch:** feat/autopilot-t3-proposer
**Spec:** docs/superpowers/specs/2026-04-30-autopilot-tier3-proposer-design.md

## File structure to create

```
src/mnemo/autopilot/proposer/
├── __init__.py          (already exists — update)
├── _git_signals.py      (git wrapper)
├── _patterns.py         (repeat-pattern detector)
├── eos_extractor.py     (end-of-session analyzer)
├── preempt.py           (pre-emptive briefing cache)
└── _hooks.py            (hook glue: schedule propose/preempt)

tests/autopilot/proposer/
├── __init__.py
├── test_git_signals.py
├── test_patterns.py
├── test_eos_extractor.py
├── test_preempt.py
├── test_hooks.py
└── test_proposer_cli.py

src/mnemo/cli/commands/autopilot.py  (extend)
src/mnemo/cli/parser.py              (extend)
src/mnemo/hooks/session_start.py     (extend — backwards compat)
src/mnemo/hooks/session_end.py       (extend)
```

## Tasks

### T1 — `_git_signals.py` + tests
Write module with:
- `git_log_since(cwd, since_iso) -> list[str]` — commit messages
- `git_diff_stat(cwd, since_ref) -> str` — short diffstat
- `git_current_branch(cwd) -> str` — HEAD branch name
- `git_status_short(cwd) -> str` — porcelain status
- All calls via `subprocess.run(capture_output=True, text=True)`, swallow `FileNotFoundError`
- Returns empty/empty-string gracefully when outside a git repo

Tests: mock subprocess.run; assert graceful fallbacks.

### T2 — `_patterns.py` + tests
Write module with:
- `extract_verb_phrases(messages: list[str]) -> Counter[str]` — extract verb+noun from commit messages using regex, no NLTK
- `find_repeated_patterns(messages, min_count=2) -> list[str]` — returns patterns occurring ≥ min_count times
- `scan_for_keywords(texts: list[str], keywords: list[str]) -> bool` — returns True if any keyword found in any text (case-insensitive)

Tests: pure unit tests with fixed message lists; no subprocess.

### T3 — `eos_extractor.py` dataclass + tests
Define:
- `@dataclass RuleCandidate` with fields: `slug_hint: str`, `title: str`, `description: str`, `confidence: float`, `sessions: list[str]`, `source: str = "tier3.eos_extractor"`
- Confidence accumulation helper `_compute_confidence(pattern_count, session_count, has_denial, has_always_keyword) -> float`
- Unit tests for confidence scoring: 0 baseline, +0.3 for ≥2 occurrences, +0.3 for ≥2 sessions, +0.2 for denial, +0.2 for always/nunca keyword; max 1.0

### T4 — vault dedup helper + tests
In `eos_extractor.py`:
- `_load_vault_slugs(vault_root: Path) -> set[str]` — reads `shared/` glob `**/*.md` frontmatter `slug:` fields
- `_is_duplicate(candidate_slug: str, existing_slugs: set[str]) -> bool` — fuzzy match (startswith + similarity ≥0.8 via ratio)
- Tests with a tmp vault populated with .md files

### T5 — `analyze_session()` core + tests
In `eos_extractor.py`:
```python
def analyze_session(*, session_id: str, project: str, vault_root: Path, cwd: Path) -> list[RuleCandidate]
```
- Calls git_log_since, git_diff_stat, find_repeated_patterns
- Reads `.mnemo/denial-log.jsonl` filtered by session_id
- Reads existing Tier 0 proposals filtered by source "tier0.*", same project
- Checks for "always"/"nunca" keywords in commit messages
- Calls _compute_confidence per candidate
- Calls _is_duplicate; skips if duplicate
- Returns candidates list (may be empty)
Tests: full integration via tmp_path; monkeypatch git signals.

### T6 — `analyze_session()` proposal writing + auto-rule threshold
- If candidate.confidence >= 0.9 AND sessions >= 2 → auto-write rule stub (`.md` in `shared/_inbox/`) PLUS write proposal
- Otherwise: write proposal only via `write_proposal(..., kind="rule_candidate", source="tier3.eos_extractor")`
- Tests: verify both proposal file creation and auto-rule file creation

### T7 — `preempt.py` predict + cache + tests
Write module with:
- `predict_next_action(*, vault_root: Path, project: str, cwd: Path) -> list[str]` — returns slugs
  - Reads git status → modified file extensions → look up related rules in rule-activation-index
  - Reads branch name → keyword match against rule slugs
  - Reads last briefing "Resume at" line → extract mentioned slugs/topics
  - Deduplicate + return top 10
- `preload_mcp_cache(*, vault_root: Path, slugs: list[str]) -> None` — no-op in v1 (hook reads cache directly)
- `write_preempt_cache(*, vault_root: Path, project: str, slugs: list[str]) -> None` — writes `.mnemo/preempt-cache.json`
- `read_preempt_cache(*, vault_root: Path) -> dict | None` — reads + validates TTL; returns None if stale/missing
- `_cache_valid(data: dict) -> bool` — checks TTL (30 min) + branch matches HEAD
- Tests: write/read roundtrip; TTL expiry; branch change invalidation; graceful fallback when missing

### T8 — `_hooks.py` + tests
Write module with:
- `schedule_eos_propose(*, vault_root: Path, session_id: str, cwd: str) -> None` — uses `schedule_autopilot_job` for eos-sweep registration
- `run_preempt_sync(*, vault_root: Path, project: str, cwd: str) -> list[str]` — calls predict_next_action + write_preempt_cache; returns slugs; swallows exceptions
- Tests: verify job registration, graceful failure

### T9 — CLI `propose` subcommand + tests
In `autopilot.py`:
- Add `_do_propose(args)` handler: reads `--session-id`, resolves vault + cwd, calls `analyze_session`, prints count
- Add `_do_preempt(args)` handler: resolves vault + cwd + project, calls `run_preempt_sync`, prints slug count
Tests: monkeypatch vault; verify rc=0, output contains counts

### T10 — CLI `proposals list` subcommand + tests
In `autopilot.py`:
- Add `_do_proposals_list(args)` handler: calls `list_proposals`, prints table (id, kind, source, confidence, status)
- Filter by `--status`, `--kind`, `--project` optional args
Tests: write 3 proposals; verify list output

### T11 — CLI `proposals review` subcommand + tests
In `autopilot.py`:
- Add `_do_proposals_review(args)` handler: shows proposal payload; prompts for accept/reject/skip via stdin; calls `update_status`
- Non-interactive mode: `--accept` or `--reject` flags skip prompt
Tests: use monkeypatched stdin; verify status update

### T12 — Parser wiring + tests
In `parser.py`:
- Extend `autopilot` subcommand: add `propose`, `preempt`, `proposals` (with `list` and `review` sub-subcommands)
- `propose` requires `--session-id ID`
- `preempt` takes no args
- `proposals list` takes optional `--status`, `--kind`, `--project`
- `proposals review` takes optional `--id ID`, `--accept`, `--reject`
Tests: parse each new subcommand; assert namespace fields populated

### T13 — `session_start.py` preempt-cache integration + tests
Extend `_build_injection_payload`:
- After building topic_lines, call `read_preempt_cache(vault_root)` (wrapped in try/except)
- If cache valid and slugs present: append `[predicted-rules session=preempt slugs=...]` block to injection
- Tests: verify payload includes predicted block when cache exists; verify no change when cache missing/stale; verify backwards compat when exception thrown

### T14 — `session_end.py` propose hook + tests
Extend `session_end.main()`:
- After `_maybe_schedule_briefing`, add `_maybe_schedule_propose(cfg, vault, agent_name, session_id=sid, cwd=cwd)`
- `_maybe_schedule_propose`: if `autopilot.enabled` in cfg → call `analyze_session` in background subprocess (detached)
- Tests: monkeypatch; verify propose is called when autopilot_cfg.propose.enabled=True; not called when disabled

### T15 — Scheduler jobs registration + tests
In `_hooks.py`:
- `register_eos_sweep_job(vault_root) -> None` — calls `schedule_autopilot_job(name="autopilot.tier3.eos-sweep", cron="*/30 * * * *", command="mnemo autopilot propose --session-id sweep")`
- Call this from `_do_on` in autopilot CLI
- Tests: verify job registered in autopilot-jobs.json

### Smoke verification checklist
After all tests green:
1. `mnemo autopilot propose --session-id dummy-001` → rc=0, prints "0 candidates" (or more if repo has signals)
2. `mnemo autopilot preempt` → rc=0, writes `.mnemo/preempt-cache.json`
3. `mnemo autopilot proposals list` → rc=0, shows table
