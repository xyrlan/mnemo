# Changelog

All notable changes to mnemo will be documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## v0.4.0 — 2026-04-14 — HOME dashboard + dimensional tags

**Added**
- **HOME.md dashboard**: `run_extraction` now regenerates a managed block inside
  `HOME.md` at vault root at the end of every run. The block groups consumer-visible
  `shared/` pages by trust tier (cross-agent synthesized first, auto-promoted
  direct reformats second) AND by topic tag. Wikilinks are path-qualified
  (`[[shared/<type>/<slug>]]`). The rest of `HOME.md` is user-owned — mnemo only
  touches content between `<!-- mnemo:dashboard:begin -->` and `<!-- mnemo:dashboard:end -->`.
- **Dimensional tags**: the extraction JSON schema gains a `tags: [topic1, topic2]`
  field. Each prompt builder now receives `vault_root` and injects a per-page-type
  "Existing vault tags" hint into the prompt so the LLM reuses the established
  vocabulary instead of inventing synonyms. Tags persist into frontmatter as a
  unified list alongside the existing system marker (`auto-promoted` /
  `needs-review`).
- **Shared filter module** (`core/filters.py`): single source of truth for
  "consumer-visible" pages — three-condition predicate (path, needs-review tag,
  stability). Both the v0.4 HOME dashboard and the planned v0.5 MCP tools will
  call the same function so human and machine views stay in lockstep. Ships with
  `collect_existing_tags(vault_root, page_type)` for the controlled-vocabulary
  hint and a minimal frontmatter parser for the exact YAML shape mnemo writes.
- **`mnemo doctor` legacy-wiki warning**: surfaces `wiki/sources/` and
  `wiki/compiled/` as orphaned v0.3 directories with a note that the next
  `mnemo extract` will auto-delete them.

**Changed**
- **`dedupe_by_slug` bug fix**: pre-v0.4 dedupe silently dropped both `stability`
  and newly-added `tags` on the floor when merging cross-chunk slug collisions.
  It now preserves `stability` from the chosen cluster and unions `tags` across
  all merged pages.
- **HOME.md template** rewritten: dashboard block skeleton at the top (after
  frontmatter), "Tier 3 — Curated wiki" section removed, `/mnemo promote` and
  `/mnemo compile` removed from quick commands. The user-editable welcome
  content sits below the managed block.
- **README template** drops references to `wiki/sources/`/`wiki/compiled/`;
  documents `HOME.md` as the landing page with an auto-generated dashboard region.

**Removed**
- **`/mnemo promote` and `/mnemo compile` CLI commands** — the manual wiki
  promotion + compilation flow is gone. The dashboard auto-regenerates as a side
  effect of `mnemo extract`, which is a superset. `cmd_promote`, `cmd_compile`,
  `core/wiki.py`, and `tests/unit/test_wiki.py` are deleted entirely.
- **`wiki/sources/` and `wiki/compiled/` directories**: scaffold no longer
  creates them; the first v0.4 `mnemo extract` on an existing vault auto-deletes
  both (and the empty `wiki/` parent if nothing else lives there). Plugin
  manifest (`.claude-plugin/plugin.json`) loses the `promote` and `compile`
  command entries.

**Invariants preserved**
- Zero new runtime dependencies — stdlib only.
- Dashboard failures never abort extraction (wrapped in try/except around an
  `OSError` boundary).
- `shared/<type>/**` remains sacred — extraction writes there, nothing else does.
- Dry-run extraction never touches `HOME.md` or deletes legacy directories.

**See:** `docs/superpowers/plans/` for the full v0.4 plan and
`project_mnemo_v0.4_direction.md` for the "Shared filter specification".

## v0.3.1 — 2026-04-14 — briefings + stability + force-wipe

**Added**
- Per-session briefings module (`core/briefing.py`) generates a structured
  shift-handoff markdown file at every session end, gated on `briefings.enabled`.
- `ExtractedPage.stability: {stable|evolving}` field populated by the LLM and
  persisted into frontmatter; the feedback system prompt teaches the LLM to
  emit `evolving` on hedging language.
- Scanner routes briefings as feedback input so the extraction pipeline mines
  the "Decisions made" and "Dead ends" sections.
- `mnemo extract --force` wipes `shared/_inbox/{feedback,user,reference}/*.md`
  to kill slug-drift duplicates from prior force runs.

**Changed**
- `minNewMemories` default lowered from 5 to 1 — with briefings producing one
  dense file per session, a single new file is enough signal for the background
  auto-spawn.

## v0.2.0 — 2026-04-13 — LLM extraction

**Added**
- `mnemo extract` command: LLM-powered consolidation of mirrored memory files
  into `shared/_inbox/` (cluster types) and `shared/project/` (1:1 promotion).
- Passive hint in `SessionEnd`: when ≥5 new memory files accumulate since the
  last extraction, today's log gets a `🟡 N new memories — run /mnemo extract`
  line (per-day dedup).
- New config section `extraction.*` with sensible defaults for model, chunk
  size, hint threshold, subprocess timeout.
- State file at `~/mnemo/.mnemo/extraction-state.json` tracks source/written
  hashes and per-slug status (`inbox`/`promoted`/`dismissed`/`direct`).

**Changed**
- `shared/` layout mirrors memory types: `shared/feedback/`, `shared/user/`,
  `shared/reference/`, `shared/project/`. The speculative v0.1 taxonomy
  (`people/`, `companies/`, `decisions/`) is deprecated. `shared/people.md`
  from v0.1 is left in place and documented as legacy.
- `core.errors.should_run()` now filters entries with `where` prefixed
  `extract.*` — manual extraction failures never trip the hook circuit
  breaker.

**Invariants preserved**
- Zero new runtime dependencies — stdlib only.
- Cross-platform (Linux / macOS / WSL / best-effort native Windows).
- Hooks never crash the Claude Code session; extraction command may fail
  loudly on stderr.
- `shared/feedback/**`, `shared/user/**`, `shared/reference/**` are sacred —
  the plugin reads them but never writes to them.

**See:** `docs/specs/2026-04-13-mnemo-v0.2-design.md` for the full design.

## [0.1.0] — TBD

### Added
- Hooks-only capture: SessionStart, SessionEnd, UserPromptSubmit, PostToolUse(Write|Edit)
- Three-tier vault: `bots/`, `shared/`, `wiki/`
- Mirror of `~/.claude/projects/*/memory/` to `bots/<agent>/memory/`
- `/mnemo` slash commands: init, status, doctor, open, promote, compile, fix, uninstall, help
- `--yes` non-interactive install for dotfiles
- Cross-platform atomic locks (`os.mkdir`-based)
- Circuit breaker (>10 errors/hour pauses hooks)
- Pure-Python rsync fallback for Windows
