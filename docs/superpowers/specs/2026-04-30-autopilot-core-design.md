# Autopilot Core — Design Spec

**Date:** 2026-04-30
**Status:** Draft for review
**Scope:** Foundational layer that enables 4 parallel Tier specs (T0/T1/T2/T3) of the "mnemo autopilot" initiative. This spec covers ONLY the shared infrastructure. Each Tier gets its own follow-up spec + plan.

## Why this exists

We're building an autonomous loop where mnemo monitors its own health (via existing telemetry), proposes changes, and opens self-fix PRs inside a safe perimeter. The 4 Tiers (insights, self-fix, tuning, proposer) are intentionally split for parallel implementation in separate worktrees. Without a shared core, each Tier would re-invent the same primitives — proposal storage, scheduling wrapper, kill switch, frozen test set, PR labeler — and produce overlapping edits to `cli.py`, `doctor.py`, and `pyproject.toml`. The core lands first, on master, so the 4 Tier branches can diverge cleanly.

The core is intentionally minimal: just enough surface for the Tiers to import. No autopilot features ship in this PR — only scaffolding.

## Non-goals

- No autopilot feature behavior (digests, self-fix PRs, tuning, proposers). All in Tier specs.
- No new telemetry events. Tiers add their own when they need to.
- No Windows shell support. `sh -c` is fine; Windows shim is a known limitation.
- No remote/multi-host coordination. Single machine, single user.

## Architecture

New top-level package `src/mnemo/autopilot/` with the structure:

```
src/mnemo/autopilot/
├── __init__.py            # public API surface
├── core/
│   ├── __init__.py
│   ├── proposals.py       # read/write .mnemo/proposals/*.json
│   ├── dispatcher.py      # thin wrapper around CronCreate / schedule for autopilot jobs
│   ├── kill_switch.py     # autopilot.json state: on/off/paused
│   ├── frozen_recall.py   # snapshot recall-cases.json → frozen.json
│   ├── pr_budget.py       # tracks auto-PRs opened today, enforces caps
│   └── labels.py          # mnemo:self-fix label constants
├── insights/              # Tier 0 — empty placeholder __init__.py
├── selffix/               # Tier 1 — empty placeholder __init__.py
├── tuner/                 # Tier 2 — empty placeholder __init__.py
└── proposer/              # Tier 3 — empty placeholder __init__.py
```

The 4 Tier directories ship as empty packages in this PR so each Tier branch can populate its directory without conflicting with the others.

## Components

### 1. Proposals store — `autopilot/core/proposals.py`

A queue of pending suggestions accumulated by Tier 0 (miss→rule candidates) and Tier 3 (end-of-session rule proposals), consumed by the same Tiers when materializing into action.

**Storage:** `.mnemo/proposals/` directory; one JSON file per proposal, named `<UTC-timestamp>-<short-hash>.json`. JSONL was rejected because we want concurrent writes from parallel agents without locking.

**Schema (v1):**

```json
{
  "schema_version": 1,
  "id": "2026-04-30T18-00-00Z-a1b2c3",
  "kind": "rule_candidate | dead_rule | doctor_warning | bm25_tune | telemetry_bug",
  "source": "tier0.miss_detector | tier1.doctor_scan | tier3.eos_extractor | ...",
  "project": "mnemo",
  "confidence": 0.0,
  "payload": { /* kind-specific shape */ },
  "status": "pending | accepted | rejected | applied | expired",
  "created_at": "2026-04-30T18:00:00Z",
  "decided_at": null,
  "applied_pr": null
}
```

**Public API:**

- `write_proposal(kind, source, payload, project=None, confidence=0.0) -> Proposal`
- `list_proposals(*, status=None, kind=None, project=None) -> list[Proposal]`
- `update_status(proposal_id, status, *, applied_pr=None) -> Proposal`
- `expire_old(days=30) -> int` — sweep helper

**Why a directory of files, not a single JSON:** parallel agents (Tier 0 digest + Tier 3 EoS extractor) can write simultaneously; lockless append-only fits the existing mnemo style (see `mcp-access-log.jsonl`, `denial-log.jsonl`).

### 2. Dispatcher — `autopilot/core/dispatcher.py`

Thin wrapper that lets a Tier register a recurring autopilot job without each Tier re-implementing scheduling.

**Public API:**

- `schedule_autopilot_job(name: str, cron: str, command: str, *, dry_run=False) -> JobHandle`
- `list_autopilot_jobs() -> list[JobInfo]`
- `cancel_autopilot_job(name) -> bool`

**Implementation:** wraps the existing `CronCreate` mechanism (the harness-side scheduler exposed in this environment). Job names are namespaced `autopilot.{tier}.{job}` so kill-switch can revoke all of them at once. Dispatcher does NOT itself execute commands; it only registers them.

For sessions where CronCreate is not available (CI tests, plain CLI runs), dispatcher operates in "record-only" mode: writes intent to `.mnemo/autopilot-jobs.json` and noops. Tests assert the recorded intent.

### 3. Kill switch — `autopilot/core/kill_switch.py`

Authoritative state for "is autopilot allowed to act right now."

**Storage:** `.mnemo/autopilot.json`:

```json
{
  "schema_version": 1,
  "state": "on | off | paused",
  "paused_until": null,
  "last_changed_at": "2026-04-30T18:00:00Z",
  "last_changed_by": "cli | auto"
}
```

**Public API:**

- `get_state() -> Literal["on", "off", "paused"]`
- `is_active() -> bool` — convenience: `state == "on" and (paused_until is None or now > paused_until)`
- `set_state(state, *, paused_until=None, source="cli") -> None`

**Auto-pause rule:** when 2 auto-PRs in a row are closed without merge by the user, `pr_budget` calls `set_state("paused", paused_until=now+24h, source="auto")`. Paused state still allows Tier 0 read-only insights but blocks Tier 1+ actions.

**CLI:** new `mnemo autopilot {on,off,pause,status}` subcommand registered in this PR (not in any Tier). Body lives in `cli/commands/autopilot.py`.

### 4. Frozen recall set — `autopilot/core/frozen_recall.py`

Tier 2 needs a fixed test set so its tuner can't optimize against drift. This module owns the snapshot.

**Public API:**

- `freeze_current(*, force=False) -> Path` — copies `.mnemo/recall-cases.json` to `.mnemo/recall-cases.frozen.json` if not already present (or if `force=True`)
- `load_frozen() -> dict` — read-only loader; raises `FrozenSetMissing` if absent
- `frozen_path() -> Path`

**Bootstrap:** on first `mnemo autopilot on`, freezes automatically. Tests cover the freeze idempotency.

### 5. PR budget — `autopilot/core/pr_budget.py`

Tracks how many auto-PRs were opened today, per category, and enforces caps.

**Storage:** `.mnemo/autopilot-budget.json` (rolling window):

```json
{
  "schema_version": 1,
  "window_start": "2026-04-30T00:00:00Z",
  "counts": { "doctor_self_fix": 0, "dead_rule_sweep": 0, "telemetry_bug": 0, "bm25_tune": 0 },
  "recent_outcomes": [
    { "pr": 99, "category": "doctor_self_fix", "outcome": "merged", "ts": "..." }
  ]
}
```

**Public API:**

- `can_open(category) -> tuple[bool, str]` — returns `(False, reason)` if budget exceeded or kill-switch tripped
- `record_opened(category, pr_number) -> None`
- `record_outcome(pr_number, outcome: Literal["merged", "closed", "abandoned"]) -> None`

**Caps:** 1 PR per category per UTC day. `recent_outcomes` keeps last 10; if last 2 of any category are `closed`, kill-switch flips to paused (24h).

### 6. Label constants — `autopilot/core/labels.py`

Just constants so all Tiers refer to the same string:

```python
SELF_FIX_LABEL = "mnemo:self-fix"
SELF_FIX_LABEL_COLOR = "0E8A16"
SELF_FIX_LABEL_DESC = "Auto-opened PR by mnemo autopilot"
```

A bootstrap helper `ensure_label_exists()` (calls `gh label create --force`) is invoked once on `mnemo autopilot on`. Idempotent.

## CLI surface added in this PR

Only one new command, `mnemo autopilot`:

```
mnemo autopilot on           # enable autopilot, freeze recall set, ensure label
mnemo autopilot off          # disable; revoke all autopilot.* cron jobs
mnemo autopilot pause [--hours N]   # temporary stop, auto-resume
mnemo autopilot status       # state + budget counters + active jobs + recent outcomes
```

All other autopilot subcommands (`mnemo autopilot digest`, `mnemo autopilot self-fix`, `mnemo autopilot tune`, `mnemo autopilot propose`) ship in their respective Tier specs.

## Data flow

```
                ┌─ Tier 0 ──┐         ┌─ Tier 3 ──┐
                │ insights  │         │ proposer  │
                └────┬──────┘         └─────┬─────┘
                     │ write_proposal       │ write_proposal
                     ▼                      ▼
              ┌─────────────────────────────────────┐
              │       autopilot/core/proposals      │
              │       .mnemo/proposals/*.json       │
              └─────────────────────────────────────┘
                     ▲                      ▲
                     │ list_proposals       │ update_status
              ┌──────┴───────┐       ┌──────┴───────┐
              │   Tier 1     │       │   digest     │
              │   selffix    │       │   reporter   │
              └──────────────┘       └──────────────┘

  kill_switch ──┐
                ▼
          gates everything (read by every Tier before acting)

  pr_budget ──── consumed by Tier 1 + Tier 2 only

  frozen_recall ── consumed by Tier 2 only
```

## Error handling

- **Missing `.mnemo/`**: every public function calls `ensure_mnemo_dir()` (existing helper from `core/paths.py`). Tests cover bootstrap from empty.
- **Concurrent writes to proposals**: per-file storage avoids it. ID collision (same ms, same hash) → suffix with `-1`, `-2`.
- **`gh` not installed**: `ensure_label_exists()` swallows and logs; autopilot still works in record-only mode for tests.
- **Cron unavailable** (CLI run, no harness): dispatcher's record-only mode kicks in. `mnemo autopilot status` shows "scheduler offline — N jobs pending".

## Testing

New test directories:

```
tests/autopilot/
├── core/
│   ├── test_proposals.py        # read/write/expire/concurrent-write
│   ├── test_dispatcher.py       # registration, namespacing, record-only mode
│   ├── test_kill_switch.py      # state transitions, paused_until honoring
│   ├── test_frozen_recall.py    # idempotent freeze, missing-set error
│   ├── test_pr_budget.py        # caps, auto-pause after 2 closed
│   └── test_labels.py           # ensure_label_exists idempotency
└── cli/
    └── test_autopilot_cli.py    # on/off/pause/status round-trip
```

Coverage target: ≥95% on `autopilot/core/*`. No integration test with real GitHub or real cron — those are exercised by Tier specs.

## Migration / compatibility

- **No schema bumps to existing artefacts.** New files only.
- **`.mnemo/autopilot.json` defaults to `{state: "off"}`** if absent — autopilot is opt-in.
- **No changes to existing CLI commands.** Only `mnemo autopilot` is added.
- **No changes to `INDEX_VERSION`, plugin manifest, npm wrapper, hooks.** Pure addition.

## Out of scope (deferred to Tier specs)

| Concern | Owner |
|---|---|
| Health digest content/format | Tier 0 |
| Doctor warning auto-fix logic | Tier 1 |
| BM25F grid search | Tier 2 |
| End-of-session rule proposer | Tier 3 |
| Pre-emptive briefing | Tier 3 |
| Reflex per-project calibration | Tier 2 |
| Dead-rule sweep heuristic | Tier 1 |

## Estimated size

- ~600 LOC production (`autopilot/core/*` + `cli/commands/autopilot.py`)
- ~800 LOC tests
- 0 changes to existing files except: `cli/commands/__init__.py` (1 import line), `cli/parser.py` (subparser block for `autopilot`), `pyproject.toml` (no version bump in this PR)

## Decisions made (with rationale)

1. **CronCreate availability:** dispatcher operates in record-only mode when scheduler is unavailable (CI, plain CLI). Tests assert recorded intent. Real cron only kicks in inside the harness.
2. **Proposal expiry:** 30 days, configurable via `.mnemo/autopilot.json` `proposal_expiry_days` field (default 30). Keeps the door open for tuning without re-shipping.
3. **`mnemo autopilot status` scope:** strictly autopilot state (on/off/paused, budget counters, active jobs, recent outcomes). Health metrics live in Tier 0's digest — keep separation of concerns.

## Next step after this spec is merged

Write 4 parallel specs (T0/T1/T2/T3) referencing this core. Each Tier opens its own worktree/branch, runs subagent-driven development, merges in order T0 → T1 → T2 → T3.
