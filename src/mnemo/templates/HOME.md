---
tags: [home, dashboard]
---
<!-- mnemo:dashboard:begin -->
## 🧠 Project brain

_The dashboard will populate on the first `mnemo extract` run._
<!-- mnemo:dashboard:end -->

# 🧠 Welcome to your mnemo vault

This vault is **populated automatically** by mnemo as you use Claude Code.
The auto-generated dashboard above scans `shared/` every time extraction
runs, so whatever Claude has learned about your work shows up here without
any curation on your part. Everything below this paragraph is yours to edit
— mnemo never touches it.

## Tier 1 — Raw capture (auto-managed)

Everything under `bots/<agent>/` is captured as you work:

- `memory/` — mirror of Claude Code memory files, one folder per repo
- `logs/` — daily append-only session logs
- `briefings/sessions/` — per-session shift handoffs written at session end (opt-in, see below)

## Tier 2 — Canonical knowledge

### Auto-populated by `mnemo extract`

The LLM consolidates cross-agent memories and session briefings into canonical pages. Single-source pages land here directly tagged `auto-promoted`; multi-source clusters stage in [[shared/_inbox]] with `needs-review` until you look them over. Each page carries a `stability` field (`stable` or `evolving`) so unsettled rules stay visible but don't pollute the dashboard. Every page also carries topic `tags` chosen by the LLM from the existing vault vocabulary.

- [[shared/feedback]] — preferences and rules the model should follow
- [[shared/user]] — user-profile facts (who you are, how you work)
- [[shared/reference]] — pointers to external systems (Linear, Grafana, Notion, etc.)
- [[shared/project]] — per-repo project context and decisions

### You maintain manually

Not touched by extraction — write here by hand when you want something canonical the LLM shouldn't rewrite.

- [[shared/people]] — people you collaborate with
- [[shared/companies]] — companies and orgs in your work context
- [[shared/decisions]] — architectural decision records (ADR-style)

## Quick commands

- `/mnemo status` — health check (includes auto-brain state)
- `/mnemo doctor` — diagnose problems
- `/mnemo extract` — manually run the consolidation pipeline (also rebuilds the dashboard above)

## Opt-in background features

Both off by default. Flip to `true` in `~/mnemo/mnemo.config.json` when you want them:

- `extraction.auto.enabled` — run `mnemo extract` automatically at session end, gated on `minNewMemories` (default 1) and `minIntervalMinutes` (default 60).
- `briefings.enabled` — generate a shift-handoff briefing at every session end. Briefings land in `bots/<agent>/briefings/sessions/<session-id>.md` and are fed back into the next extraction run as dense input.
