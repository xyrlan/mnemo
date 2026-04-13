---
tags: [home, dashboard]
---
# 🧠 Welcome to your mnemo vault

This vault is **populated automatically** by mnemo as you use Claude Code.
The only dirs you edit by hand are the user-maintained Tier 2 ones below.

## Tier 1 — Raw capture (auto-managed)
- [[bots]] — daily logs and Claude memory mirror, one folder per repo

## Tier 2 — Canonical knowledge

### Auto-populated by `mnemo extract`
The LLM consolidates cross-agent memories into canonical pages. Single-source pages land here directly tagged `auto-promoted`; multi-source clusters stage in [[shared/_inbox]] with `needs-review` until you look them over.
- [[shared/feedback]] — preferences and rules the model should follow
- [[shared/user]] — user-profile facts (who you are, how you work)
- [[shared/reference]] — pointers to external systems (Linear, Grafana, Notion, etc.)
- [[shared/project]] — per-repo project context and decisions

### You maintain manually
Not touched by extraction — write here by hand when you want something canonical the LLM shouldn't rewrite.
- [[shared/people]] — people you collaborate with
- [[shared/companies]] — companies and orgs in your work context
- [[shared/decisions]] — architectural decision records (ADR-style)

## Tier 3 — Curated wiki
- [[wiki/sources]] — promoted notes
- [[wiki/compiled]] — regeneratable index

## Quick commands
- `/mnemo status` — health check (includes auto-brain state)
- `/mnemo doctor` — diagnose problems
- `/mnemo extract` — manually run the consolidation pipeline
- `/mnemo promote <file>` — move a note into the wiki
- `/mnemo compile` — regenerate the wiki index
