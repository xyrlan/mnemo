# Autopilot Tier 3 — Rule Proposer — Design Spec

**Date:** 2026-04-30
**Status:** Draft for review
**Depends on:** `autopilot-core` (proposals queue), `autopilot-tier0-insights` (consumes miss collector output)

## Why

mnemo today extracts rules from session transcripts via the existing extraction pipeline. Tier 3 goes further: it **synthesizes rule candidates** from signals the user hasn't explicitly articulated.

Two features:

1. **End-of-session rule proposer** — at session end, scan diff + commits + denials + prompts. Detect repeated patterns ("você fez X 3× sem rule"). Propose candidates.
2. **Pre-emptive briefing** — when user opens a repo, predict next action from `git status` + branch + last briefing, pre-load relevant rules into MCP cache before first prompt arrives.

## Components

### 1. `autopilot/proposer/eos_extractor.py`

```
analyze_session(*, session_id, project) -> list[RuleCandidate]
```

**Signal sources:**
- `git log --since='session_start'` — commit messages (look for repeated verbs: "fix typo", "add validation")
- `git diff session_start..HEAD` — diff stats (look for repeated additions of same import/pattern)
- `denial-log.jsonl` filtered by session_id — user-overrode denials? deserves a counter-rule?
- session transcript prompts — repeated phrases?
- existing rules in vault — if a candidate would duplicate one, raise dup
- consumes `rule_candidate` proposals from Tier 0 — promotes to higher confidence if the same pattern recurs

**Output:** writes proposals with `kind="rule_candidate"`, `source="tier3.eos_extractor"`, `confidence` 0.0–1.0 based on:
- `+0.3` if pattern occurs 2+ times
- `+0.3` if appears in ≥2 sessions
- `+0.2` if a denial was logged
- `+0.2` if user explicitly used the word "always" or "nunca" in a prompt

Confidence ≥0.9 + appears in ≥2 sessions → auto-write rule file (still requires human merge of the PR opened by Tier 1).

### 2. `autopilot/proposer/preempt.py`

```
predict_next_action(*, vault_root, project) -> list[str]   # rule slugs
preload_mcp_cache(*, vault_root, slugs) -> None
```

**Inputs for prediction:**
- `git status` (modified files → infer topic)
- `git rev-parse --abbrev-ref HEAD` (branch name → infer feature/fix/refactor)
- last briefing's "Resume at" line
- recent prompts in this project

**Output:** list of likely-relevant rule slugs.

**Cache mechanism:** writes to `.mnemo/preempt-cache.json`:
```json
{
  "predicted_at": "2026-04-30T19:00:00Z",
  "project": "Meunu",
  "slugs": ["fix-product-price-nan-normalization", "..."],
  "ttl_minutes": 30
}
```

`mnemo init` reads this file at SessionStart and includes the predicted rules in the briefing envelope (with a marker so user knows it's predicted, not history).

### 3. CLI extensions

```
mnemo autopilot propose --session-id ID
mnemo autopilot preempt          # one-shot prediction; writes preempt-cache.json
mnemo autopilot proposals list
mnemo autopilot proposals review [--id ID]   # show + accept/reject one
```

### 4. Hook integration

- **SessionEnd hook** → `mnemo autopilot propose --session-id $ID` (auto-runs when supported)
- **SessionStart hook** → `mnemo autopilot preempt` BEFORE the briefing inject, so cache is fresh

### 5. Scheduled jobs

- `autopilot.tier3.eos-sweep` — `*/30 * * * *` (every 30 min) — picks up sessions that ended without explicit hook
- `autopilot.tier3.preempt-refresh` — only triggered by SessionStart, no cron

## File structure

```
src/mnemo/autopilot/proposer/
├── __init__.py
├── eos_extractor.py
├── preempt.py
├── _patterns.py     # repeat-pattern detector (used by eos_extractor)
├── _git_signals.py  # thin git wrapper (status, log, diff)
└── _hooks.py        # SessionStart/SessionEnd hook glue
```

Estimated: ~800 LOC prod, ~1000 LOC tests.

## Risks

- **Noisy proposals.** Mitigation: confidence threshold + dedup against existing rules + per-pattern occurrence count ≥2.
- **Preempt cache stale.** Mitigation: 30 min TTL + invalidated on branch change.
- **Hook fails silently.** Mitigation: scheduled `eos-sweep` catches missed sessions.

## Out of scope

- Auto-merge of proposed rules (always via Tier 1 PR).
- Cross-project pattern transplant (would be Tier 5/B from earlier brainstorm).
- LLM-based pattern detection (keep heuristic-only for v1).

## Spec self-review

- ✅ No TBD
- ✅ Confidence model is explicit + testable
- ✅ Cache TTL prevents stale predictions
- ✅ Scope ≤ 800 LOC
