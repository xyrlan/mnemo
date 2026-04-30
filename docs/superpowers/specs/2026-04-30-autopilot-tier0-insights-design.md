# Autopilot Tier 0 — Insights — Design Spec

**Date:** 2026-04-30
**Status:** Draft for review
**Depends on:** `autopilot-core` (PR #70 — must merge first)

## Why

Tier 0 is read-only: it produces signals that downstream Tiers act on. Two features:

1. **Health digest** — weekly markdown summary of recall delta, reflex emit-rate, denial counts, dead-rule candidates, regras one-adoption-from-promotion. Posted to vault as a markdown note + optional GitHub issue. Closes the loop between telemetry-already-collected and human visibility.
2. **Miss → rule candidate accumulator** — every time `mnemo recall` records a miss, a `rule_candidate` proposal is appended to `.mnemo/proposals/`. Tier 3 consumes those; Tier 0 only writes them.

Tier 0 ships zero PR-opening behavior — it is a pure observer. Lowest blast radius of all four Tiers.

## Non-goals

- No PR opening (Tier 1).
- No tuning (Tier 2).
- No rule synthesis from prompts/diffs (Tier 3).
- No new metrics — only re-formatting telemetry already collected by mnemo today.

## Components

### 1. `autopilot/insights/digest.py`

```
generate_digest(*, vault_root, since_days=7) -> Digest
write_digest(*, vault_root, digest) -> Path   # writes markdown to vault/briefings/autopilot/<date>.md
post_digest_issue(*, digest) -> Optional[int]  # gh issue create; None if gh missing
```

Sections in the digest markdown:

```
# Autopilot weekly digest — YYYY-MM-DD

## Recall
- primacy@5: 90.0% (Δ +0.0pp vs last week)
- MRR: 0.5563 (Δ +0.0)
- p95 latency: 3 ms

## Reflex
- prompts: 1346 (last 7d)
- emit-rate: 5.6%  (target band: 3-12%)
- top silence reasons: relative_gap_fail (455), below_min_tokens (295), absolute_floor_fail (186)
- index_missing: 133  ⚠

## Denials
- last 7d: 5
- top blocker: Supabase: Single Production Database (4)

## Health flags
- 244 rules one-adoption-from-promotion
- 13 source-paths broken in shared/feedback/
- 2 enforce blocks stripped (auto-promote)
- llm.call cost_usd field is 0 — telemetry bug?

## Top emitted rules (last 7d)
- Canonical refactoring workflow (4)
- Solo dev auto-mode workflow (3)
- ...
```

**Inputs:** `mcp-access-log.jsonl`, `reflex-log.jsonl`, `denial-log.jsonl`, `recall-report.json`, `mnemo doctor --json` (already exists). No new instrumentation.

**Output path:** `<vault>/briefings/autopilot/<YYYY-MM-DD>-digest.md`. Lives under briefings so it becomes searchable + linkable in Obsidian.

### 2. `autopilot/insights/miss_collector.py`

```
collect_recall_misses(*, vault_root) -> int   # scan recall-report.json, write rule_candidate proposals
```

For each miss in `recall-report.json`, write a proposal:

```json
{
  "kind": "rule_candidate",
  "source": "tier0.miss_collector",
  "project": "<project>",
  "confidence": 0.0,
  "payload": {
    "expected_slug": "...",
    "topic": "...",
    "reason": "miss in recall — ranked N/M",
    "recall_report_at": "2026-04-30T18:38:57Z"
  }
}
```

Idempotent: skip writing if a proposal with the same `expected_slug` + `project` is already pending.

### 3. CLI: `mnemo autopilot digest`

```
mnemo autopilot digest               # generate + print path; do not post to GitHub
mnemo autopilot digest --post        # also gh issue create
mnemo autopilot digest --since 30d   # custom window
mnemo autopilot collect-misses       # one-shot run of miss_collector
```

### 4. Scheduled job

`mnemo autopilot on` registers (via `core.dispatcher.schedule_autopilot_job`) the following:

- `autopilot.tier0.digest` — `0 9 * * 1` (weekly Monday 09:00 UTC) → runs `mnemo autopilot digest --post`
- `autopilot.tier0.collect-misses` — `0 8 * * *` (daily 08:00 UTC) → runs `mnemo autopilot collect-misses`

Record-only mode means these are persisted to `.mnemo/autopilot-jobs.json`; real cron fires when running inside the harness.

## Tests

- `tests/autopilot/insights/test_digest.py` — fixture inputs (synthetic logs), assert markdown sections present + numbers correct
- `tests/autopilot/insights/test_miss_collector.py` — assert proposal written per miss; assert idempotency
- `tests/autopilot/insights/test_digest_cli.py` — CLI round-trip, `--post` mocked

## File structure

```
src/mnemo/autopilot/insights/
├── __init__.py
├── digest.py
├── miss_collector.py
└── _formatters.py     # number formatting, delta arrows, etc.

src/mnemo/cli/commands/autopilot.py    # extended with `digest` + `collect-misses` subactions
```

Estimated: ~400 LOC prod, ~500 LOC tests.

## Risks

- **Digest accuracy depends on log integrity.** If `mcp-access-log.jsonl` has gaps, digest will be wrong. Mitigation: digest reports raw counts, no derived rates without sample size annotation.
- **Issue spam.** `--post` could repeatedly file issues. Mitigation: only post when the previous issue (label `mnemo:digest`) is closed.

## Out of scope (deferred)

- Per-project digests (single global digest only).
- Slack/email delivery.
- Trend graphs (markdown text only).

## Spec self-review

- ✅ No TBD/TODO
- ✅ Each component has clear interface
- ✅ Scope: read-only, single PR, ~400 LOC — fits one plan
- ✅ Idempotency explicit (miss collector + post-issue)
