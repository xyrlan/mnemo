# mnemo vault

This vault was scaffolded by [mnemo](https://github.com/xyrlan/mnemo) — a Claude Code
plugin that captures every session into a local Obsidian-compatible markdown vault.

## Layout

- `bots/<agent>/memory/` — mirror of Claude Code memory files (plugin-managed)
- `bots/<agent>/logs/` — daily append-only session logs (plugin-managed)
- `bots/<agent>/briefings/sessions/` — per-session shift handoffs when `briefings.enabled=true`
- `shared/feedback|user|reference|project/` — canonical Tier 2 pages auto-populated by `mnemo extract`
- `shared/_inbox/` — multi-source merges staged for review before promotion
- `shared/people|companies|decisions/` — user-maintained Tier 2 (extraction never touches these)
- `HOME.md` — landing page with an auto-generated dashboard block (between `<!-- mnemo:dashboard:begin -->` and `<!-- mnemo:dashboard:end -->`) that scans `shared/` after every extraction. The rest of `HOME.md` is yours to edit.

## Open in Obsidian

Point Obsidian at this folder. The bundled `graph-dark-gold.css` snippet styles
the graph view if you enable it under Settings → Appearance → CSS Snippets.

## Privacy

Everything lives on your filesystem. No telemetry, no analytics, no vault data
leaves your machine on its own.

The optional auto-brain (`extraction.auto.enabled`) and briefing (`briefings.enabled`)
features — **off by default** — invoke Claude Code's `claude --print` subprocess,
which calls Anthropic's API under your own subscription or API key. When those
flags are on, the contents of your `bots/<agent>/memory/*.md` and
`bots/<agent>/briefings/sessions/*.md` files get sent to Anthropic as part of
the extraction/briefing prompts. Everything else — scaffolding, log writes,
memory mirrors, `mnemo status`, `mnemo doctor` — runs fully offline.

Delete this folder anytime.
