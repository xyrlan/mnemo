# Changelog

All notable changes to mnemo will be documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- `rule-activation-index` schema bumped from v2 to v3. The v0.8.x
  `file_stem` field was added without a version bump, so existing
  v2 indexes silently fall back to slow glob scanning. v3 forces
  a transparent rebuild on first load (already-load-bearing
  auto-rebuild path: `load_validated_json` returns `None` on
  schema mismatch; SessionStart and extract hooks call
  `build_index` whenever `load_index` returns `None`). First run
  after upgrade takes a few seconds longer; nothing else visible.
  ([refactor roadmap PR E](docs/superpowers/plans/2026-04-19-refactor-roadmap.md))

### Removed

- `mnemo.core.mcp.counter` v0.8 backwards-compat shim. Importers must use
  `mnemo.core.mcp.session_state` directly. The shim was scheduled for
  v0.9 removal in the v0.8 CHANGELOG. ([refactor roadmap PR D](docs/superpowers/plans/2026-04-19-refactor-roadmap.md))

### Internal

- `mnemo.core.rule_activation` monolith (849 LOC) split into a package:
  `parsing.py`, `globs.py`, `matching.py`, `index.py`, `activity_log.py`,
  plus a back-compat shim at `__init__.py`. The pre-v0.9 import surface
  is preserved. `parse_enforce_block` + `parse_activates_on_block` +
  `_describe_*_error` collapsed into a single `parse_block(kind, fm)`
  walker (the two thin wrappers stay for back-compat; the two describe
  helpers are deleted). `_is_universal` promoted to public `is_universal`
  (single in-tree consumer at `reflex/index.py` updated atomically; no
  deprecation window). `build_index` orchestrator decomposed via a new
  `_build_rule_entry` helper (138L → <30L).
  ([refactor roadmap PR G](docs/superpowers/plans/2026-04-19-refactor-roadmap.md))

## v0.8.0 — 2026-04-19 — Prompt Reflex

### Added

- **UserPromptSubmit Reflex**: new hook that injects 0-2 rule body previews
  inline via BM25F retrieval when a triple-gate confidence test passes.
  Scope respects v0.7 semantics (local + universal per project).
- **`aliases:` frontmatter field**: optional synonym bridge for bilingual
  or domain-synonym matching. Extraction LLM emits it across all three
  system prompts.
- **`reflex` config block**: full tuning surface for thresholds, BM25F
  parameters, field weights, and kill switches (`reflex.enabled`).
- **`mnemo doctor` reflex checks**: `reflex-index-stale`,
  `reflex-session-cap-hit`, `reflex-bilingual-gap`.
- **Statusline**: new `N⚡` segment aggregating today's reflex emissions.

### Changed

- `mcp-call-counter.json` extended in place with `injected_cache` and
  `session_emissions` top-level keys. File path preserved for
  backwards-compatibility with v0.7 statusline + server readers.
- `counter.py` Python module renamed to `session_state.py` with a thin
  compat shim. The shim will be removed in v0.9.
- `PreToolUse` enrichment now honours `enrichment.maxEmissionsPerSession`
  (default 15) and filters against the shared `injected_cache` to avoid
  cross-hook duplicate injections.

### Defaults

- `reflex.enabled = true` by default in v0.8.0 stable. Kill switch: set
  `"reflex": {"enabled": false}` in `mnemo.config.json`.
- `reflex` config block scoped to the knobs that are actually wired:
  `enabled`, `maxEmissionsPerSession`, `thresholds`, and `bm25f`. Additional
  knobs (maxHits, previewChars, dedupeTtlMinutes, log.maxBytes,
  debug.logRawPrompt) are deferred until v0.9 when they'll be wired.

## v0.7.0 — 2026-04-18

### Breaking

- `scope="project"` on MCP retrieval (`list_rules_by_topic`,
  `read_mnemo_rule`, `get_mnemo_topics`) now returns **local + universal**
  rules. Pass `scope="local-only"` to preserve v0.6.2 "strict local" behaviour.
- Rule-activation index schema bumped to v2. Existing v1 indexes load as
  `None` and are regenerated automatically on the next SessionStart.
- Index top-level keys `enforce_by_project` and `enrich_by_project` are
  removed. Consumers read the unified `rules` table plus the derived
  `by_project` / `universal` lookup tables, or use the new iterators
  `iter_enforce_rules_for_project` / `iter_enrich_rules_for_project`.

### Note on MCP fallback

If MCP is invoked *before* the first SessionStart of v0.7.0 has rebuilt the
index, retrieval falls back to a glob+parse walk of `shared/{feedback,user,reference}/`.
In that fallback path, **universality is not evaluated** — every rule is
treated as local (equivalent to `scope="local-only"`). Running a SessionStart
(or `mnemo extract`) after upgrade is all that's needed to enable the full
v0.7 semantics.

### Added

- Automatic **universal promotion** at `distinct_projects >= 2`
  (configurable via `scoping.universalThreshold`).
- Structured `mnemo://v1` SessionStart injection envelope with per-scope
  topic lines and a `injection.maxTopicsPerScope` cap (default 15).
- `mnemo doctor` reports universal promotion health.
- `shared/project/` pages now carry `runtime: false` to document their
  human-surface-only role.

### Changed

- MCP retrieval now reads the unified index for O(1) lookups; glob+parse is
  kept as a fallback for missing/stale indexes.
- SessionStart rebuilds the index whenever `injection.enabled` is true
  (in addition to the existing enforcement/enrichment triggers).

## v0.6.0 — 2026-04-16 — Loop enabled by default

**Changed**
- Defaults flipped from `false` → `true` for `extraction.auto.enabled`,
  `briefings.enabled`, `injection.enabled`, and `enrichment.enabled`.
  `mnemo init` now produces a working product from session one, instead
  of an inert scaffold awaiting manual JSON configuration.
- `enforcement.enabled` was already `true` since v0.5; unchanged.

**Backward compatibility**
- `_deep_merge` in `core/config.py` preserves explicit `enabled: false`
  values in existing user configs. Users who had opted out of specific
  features continue to see opt-out behavior with no action required.

**Rationale**
- The opt-in pattern shipped since v0.3 ("ship dark, dogfood, then flip")
  imposed JSON-editing friction without safety benefit during solo
  dogfood, and contradicted the project tagline ("the Obsidian that
  populates itself"). Flipping defaults aligns the zero-config experience
  with the product promise.

**Migration**
- Users who wanted the features on: no action needed — defaults now match
  your existing explicit config.
- Users who wanted the features off: add `"enabled": false` blocks to
  `~/mnemo/mnemo.config.json`. See README "Runtime flags".

**Tests**: 779 passing, 2 skipped (opt-in E2E only).

## v0.5.0 — 2026-04-15 — MCP injection (the loop closes)

**Added**
- **MCP stdio server** (`src/mnemo/core/mcp/`): a long-running JSON-RPC 2.0
  process exposing three read-only tools to Claude Code:
  - `list_rules_by_topic(topic)` — returns slugs sorted by source_count desc
    so multi-agent synthesized rules surface first
  - `read_mnemo_rule(slug)` — returns the full body + frontmatter for a slug
  - `get_mnemo_topics()` — returns the union of all topic tags in the vault
  Hand-rolled stdlib-only (no `mcp` SDK dependency, consistent with mnemo's
  `dependencies = []` policy). Both tools apply the v0.4 shared filter from
  `core/filters.py` so machine view and the HOME dashboard stay in lockstep.
  Project pages are excluded by design: they have no topic tags by
  construction and their sources are already in Claude's auto-memory.
- **SessionStart MCP topic injection**: when `injection.enabled=true`, the
  SessionStart hook emits a `hookSpecificOutput.additionalContext` JSON
  envelope listing the vault's topic tags plus a one-line instruction
  telling Claude to call `list_rules_by_topic` + `read_mnemo_rule` BEFORE
  writing code when the task matches a known topic. ~120 tokens per session
  regardless of vault size.
- **`mnemo init` registers the MCP server in `~/.claude.json`** under
  `mcpServers.mnemo`. `mnemo uninstall` removes it. Fully idempotent.
- **New config flag `injection.enabled`** (default `false`, opt-in per the
  v0.3 conservative pattern). Flip to `true` in `~/mnemo/mnemo.config.json`
  to activate after dogfood validates the injection mechanism in your vault.
- **Hidden CLI subcommand `mnemo mcp-server`**: stdio entry point referenced
  from `~/.claude.json`. Not surfaced in `mnemo --help`.

- **Status line integration**: `mnemo init` now wires an additive
  `statusLine` composer into `~/.claude/settings.json`. Output looks like
  `mnemo mcp · 9 topics · 7↓ today` — topic count from your vault plus
  the number of times Claude has consulted mnemo via MCP today (counter
  resets daily, atomic write, lives in `<vault>/.mnemo/mcp-call-counter.json`).
  If you already had a custom statusLine, mnemo **does not overwrite it** —
  the composer wraps your original command and concatenates outputs with
  ` · `. Your original is preserved in `<vault>/.mnemo/statusline-original.json`
  and restored by `mnemo uninstall`. `mnemo doctor` warns if you edit
  settings.json manually and drift away from the composer.

**Internal**
- Injection mechanism de-risked on 2026-04-15 via a throwaway prototype that
  proved `hookSpecificOutput.additionalContext` injects into interactive
  Claude sessions, not just `claude --print` one-shot mode. The prototype is
  removed in this release.

**Tagline status**: "Capture → Present → Inject" is now complete. v0.3
shipped capture, v0.3.1 shipped dense input (briefings), v0.4 shipped
auto-presentation (HOME dashboard + tags), v0.5 ships auto-injection.

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
