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

Pages under [[shared/project]] are a **human navigation surface only** — they
carry `runtime: false` in their frontmatter. Project-specific runtime context
reaches Claude via native auto-memory over `bots/<agent>/memory/`.

## Quick commands

- `/mnemo:status` — health check (includes auto-brain state)
- `/mnemo:doctor` — diagnose problems
- `mnemo extract` — manually run the consolidation pipeline (also rebuilds the dashboard above)

Installed via npm or pipx instead of the plugin? Drop the `:` and run them in a
terminal: `mnemo status`, `mnemo doctor`.

## Background features

All **on by default** — mnemo is a working product out of the box, not a
scaffold you have to switch on. Set any of these to `false` in
`mnemo.config.json` to opt out:

- `extraction.auto.enabled` — run extraction automatically at session end, gated on `minNewMemories` (default 1) and `minIntervalMinutes` (default 60).
- `briefings.enabled` — generate a shift-handoff briefing at every session end. Briefings land in `bots/<agent>/briefings/sessions/<session-id>.md` and feed the next extraction run as dense input.
- `injection.enabled` — tell Claude about your mnemo brain at session start: a topic list plus the `list_rules_by_topic` / `read_mnemo_rule` MCP tools, so a new session can't fail to know your rules.
- `reflex.enabled` — retrieve and inject the single most relevant rule on every prompt.

Full reference: https://github.com/xyrlan/mnemo/blob/master/docs/configuration.md
