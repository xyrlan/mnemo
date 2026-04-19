# Refactor Plan — mnemo v0.9 (post v0.8.0)

## Context
- **State**: master at 9e8155b, v0.8.0 stable shipped, 963 tests passing, file_stem regression fixed (PR #38), `load_validated_json` + `_find_rule_file_by_slug` extracted (PR #40).
- **Scope**: refactor-only. No new features. Target: reduce duplication, split god-modules, fix SOLID violations surfaced by a 5-agent swarm audit (cli.py, rule_activation.py, inbox.py, prompts.py, v0.9 debt).
- **Constraints (CLAUDE.md)**: files <500 lines, functions <80 lines, <500 LOC / PR preferred, keep `mnemo.cli:main` entry point intact.

## Findings (summary, from swarm)

### File sizes over limit
| File | Lines | Biggest offender |
|------|------:|------------------|
| `src/mnemo/cli.py` | 1375 | `_doctor_check_activation` (112L) |
| `src/mnemo/core/rule_activation.py` | 849 | `build_index` (138L) |
| `src/mnemo/core/extract/inbox.py` | 755 | `_apply_inbox` (96L), `_apply_auto_promoted` (77L) |
| `src/mnemo/core/extract/prompts.py` | 529 | `_FEW_SHOT_FEEDBACK` (152L template) |

### Duplication hot-spots
- **D1**: Path shape `vault_root / "shared" / type / f"{slug}.md"` inlined in inbox.py:195/199/209, inbox.py:366-370, inbox.py:414-418, promote.py:22 (5 sites).
- **D2**: `.proposed.md` sibling pattern — inbox.py:236, promote.py:101, inbox.py:679-680 (3 sites).
- **D3**: Content-hash one-liner `"sha256:" + hashlib.sha256(...)` at inbox.py:498, 576, promote.py:63 (3 sites).
- **D4**: `StateEntry` fresh-write pattern at inbox.py:501-510, 580-589, 596-604, 645-653, promote.py:66-74, 79-86 (6 sites).
- **D5**: `SCHEMA_VERSION = 2` literal in inbox.py:18 AND scanner.py:39 (independent sources of truth).
- **cli-duplication**: frontmatter-parse pattern inlined 4x at cli.py:591, 677, 801, 996; `_read_*_log_tail` functions are near-identical.

### SOLID violations
- **SRP**: `_doctor_check_activation` bundles 4 independent checks; `build_index` does 10+ concerns; `_apply_inbox` handles 4 disjoint statuses; `prompts.py` mixes templates with transcript parsing.
- **OCP**: `cmd_doctor` hard-coded `and`-chain; `apply_pages` hard-coded if/elif; three `build_*_prompt` functions near-identical.
- **Encapsulation leak**: `reflex/index.py:37` imports private `_is_universal` and `projects_for_rule` from `rule_activation` (underscore-prefixed = private by convention).

### v0.9 debt
- `src/mnemo/core/mcp/counter.py` shim (13 lines, "remove in v0.9", 2 production + 3 test callers).
- `INDEX_VERSION = 2` but `file_stem` field was added without bump → old indexes silently fall back to slow glob.

## Plan — 9 PRs in 3 waves (+ gating PR 0)

### PR 0 — Public API surface test (gate for every shim in Wave 3)
- **Why**: Every Wave 3 PR converts a module into a package with a back-compat shim. A missed re-export is a silent ImportError waiting to fire. Without a surface test, shim correctness is verified only by existing tests, which don't exercise every import-site exhaustively.
- **Add**: `tests/unit/test_public_api_surface.py`. For each module scheduled for package conversion (`mnemo.cli`, `mnemo.core.rule_activation`, `mnemo.core.extract.inbox`, `mnemo.core.extract.prompts`), enumerate every name currently imported by any in-repo caller (run `grep -r "from <module> import" src/ tests/` at PR-0 time and freeze the list), then assert each name resolves via `importlib`.
- **LOC**: ~80, one test file.
- **Blocks**: all Wave 3 PRs (F, G, H, I). Wave 1 and 2 don't need it because they don't convert modules into packages.
- **Risk**: zero — test-only, adds a guardrail.

### Wave 1 — pure extractions (LOW risk, parallelizable)

**PR A — `cli_helpers.py`**
- Move: `_read_denial_log_tail` (cli.py:317-341) + `_read_enrichment_log_tail` (cli.py:420-445) merged into one `_read_jsonl_tail(path, max_lines)` with two thin wrappers; `_synthesize_path_for_glob` (cli.py:539-555); `_count_today_denial_entries` (cli.py:342-351).
- Location: `src/mnemo/cli_helpers.py` (flat, single-file, pass-1).
- LOC moved: ~78.
- Behavior: zero change. Re-export from cli.py top-of-file.
- Tests: no edits needed (pure functions not monkeypatched).

**PR B — `extract/inbox_paths.py`**
- Move: `_inbox_path`, `_promoted_path`, `_target_path_for_page`, `_is_auto_promoted_target`, `_sibling_path` (inbox.py:194-236).
- Location: `src/mnemo/core/extract/inbox_paths.py`.
- LOC moved: ~43.
- Behavior: zero change. Inbox.py re-imports.
- Unblocks: PR-future consolidation of D1/D2/D3 with promote.py.

**PR C — DROPPED.**
- Original plan extracted `log_denial` / `log_enrichment` into a fresh `rule_activation/activity_log.py` subpackage as a "proof of shim". Review found this created a Wave 1 ⇄ Wave 3 layout coupling (resolved as C2 in the review) — Wave 3 PR G does the full split and would either have to preserve PR C's pre-placed file or move it, adding risk and/or wasted work.
- **Decision**: fold `activity_log.py` extraction into PR G. Wave 1 is now 2 PRs (A + B).

### Wave 2 — v0.9 debt removal (LOW risk, sequential after Wave 1)

**PR D — Remove `counter.py` shim**
- Delete `src/mnemo/core/mcp/counter.py` (13 lines).
- Delete `tests/unit/test_session_state_shim.py`.
- Rewrite 5 import sites: `server.py:25`, `statusline.py:143`, `test_mcp_counter.py`, `test_statusline.py:85`, `test_mcp_server.py:298,323` → use `session_state` directly.
- Rename `test_mcp_counter.py` → `test_session_state_counter.py`.
- CHANGELOG: v0.9 Removed section.
- LOC delta: −50.

**PR E — `INDEX_VERSION` 2 → 3**
- `rule_activation.py:35`: `INDEX_VERSION = 3`.
- Update `tests/unit/test_rule_activation_index.py:361` fixture, `test_rule_activation_match.py:672` hard-coded assertion.
- CHANGELOG: **"Changed / Internal"** section (not "Breaking") — the rebuild is transparent to users; `load_validated_json` returns None on schema_version miss, and SessionStart + extract hooks already call `build_index` on None. First run after upgrade takes a few seconds longer; nothing else visible.
- Behavior: auto-rebuild path is already load-bearing for v1→v2; reusing it for v2→v3 costs zero new code.

### Wave 3 — structural decomposition (MEDIUM risk, sequential)

**Order (revised after review W3)**: **G → I → F1 → F2 → H**.

Rationale: PR G delivers the highest value-per-effort (unifies parsing, fixes encapsulation leak, closes the module-docstring debt). PR I kills 5 duplication clusters (D1–D5). F is split into a safety-net test (F1) that must land standalone so F2's template extraction can be bisected cleanly. H is last because its risk is organizational (test monkeypatch surface), not correctness — and by the time it runs, PR 0's surface test is proven.

**PR G — `rule_activation/` package** *(highest-value Wave 3 PR — goes first)*
- Convert `rule_activation.py` → `rule_activation/` package:
  - `parsing.py` — ReDoS helpers (`_CATASTROPHIC_SUBSTRINGS`, `_REDOS_*`, `_pattern_is_redos_safe`), `parse_enforce_block`, `parse_activates_on_block`, `_VALID_ENRICH_TOOLS`, `_MATCH_FLAGS`. ~220L.
  - `globs.py` — `_glob_matches`, `_glob_to_regex` (leaf module; imported by parsing + matching, so sits below both). ~110L.
  - `index.py` — `INDEX_VERSION`, `INDEX_FILENAME`, `build_index`, `load_index`, `write_index`, `projects_for_rule`, `is_universal` (promoted from `_is_universal` — see below), `_atomic_write_bytes`, `_SYSTEM_TAGS`. ~230L.
  - `matching.py` — `EnforceHit`, `EnrichHit`, `normalize_bash_command`, `match_bash_enforce`, `match_path_enrich`, `iter_enforce_rules_for_project`, `iter_enrich_rules_for_project`. ~230L.
  - `activity_log.py` — `log_denial`, `log_enrichment` (absorbed here, originally planned as PR C). ~80L.
  - `__init__.py` — shim re-exporting the full public surface (every name that PR 0's surface test enumerated). Kept permanently for back-compat.
- **Unify `parse_*_block` + `_describe_*_error`** into `parse_block(kind: Literal["enforce", "activates_on"], fm) → (parsed, error)`. Thin wrappers preserve existing `parse_enforce_block` / `parse_activates_on_block` callers. `_describe_enforce_error` / `_describe_enrich_error` are deleted — the unified walk produces the message on the failing step. Net reduction: ~80 lines across 4 functions.
- **`build_index` decomposition**: extract `_build_rule_entry(md_path, vault_root, threshold) → RuleEntry | MalformedEntry`. Orchestrator drops from 138L to <30L.
- **Encapsulation leak fix (in-PR, no deprecation window)**: rename `_is_universal` → `is_universal` (public) in the new `index.py`, and update the single consumer `src/mnemo/core/reflex/index.py:37` in the same PR. `projects_for_rule` is already un-prefixed; no rename needed. No back-compat re-export for `_is_universal` — it was private by naming convention, and the only external caller is an in-repo sibling that the PR fixes atomically.
- **Gate**: PR 0's surface test must pass. Expected surface includes all the names reflex/cli/statusline/mcp-tools/hooks import today (grep once before writing the PR).

**PR I — `inbox/` package** *(kills 5 duplication clusters; goes second)*
- Convert `inbox.py` → `inbox/` package:
  - `inbox/io.py` — `atomic_write`, `content_hash` (new **public** names; kills D3 and D4 consolidation target). Keep underscore aliases `_atomic_write`, `_file_hash` as deprecated re-exports with a `DeprecationWarning`, scheduled for removal in v0.10.
  - `inbox/rendering.py` — `_yaml_*`, `_render_nested_block`, `_render_page`, `_extract_body`. ~140L.
  - `inbox/paths.py` — already extracted in PR B; this PR just adds the new package-internal re-export. Also refactors `_target_path_for_page` (currently inlines the path shape at inbox.py:209-210) to call `_inbox_path` / `_promoted_path` (kills the D1 inline at source).
  - `inbox/dedup.py` — `dedupe_by_slug`, `_bodies_similar`, `_stem_word`, `_stem_slug`, `_detect_stem_collision`, `_detect_drift_slug`. ~200L. Replaces the inline `vault_root / "shared" / type / f"{slug}.md"` duplicates in `_detect_stem_collision` and `_detect_drift_slug` (inbox.py:366-370, 414-418) with calls to `paths._promoted_path` / `paths._inbox_path`.
  - `inbox/apply.py` — `apply_pages` as a table-driven dispatcher (OCP fix: `{status_predicate → handler}` map at module scope). ~80L.
  - `inbox/branches/auto_promoted.py` — split `_apply_auto_promoted` (77L) into smaller per-status helpers. ~130L.
  - `inbox/branches/inbox_flow.py` — split `_apply_inbox` (96L) into `_handle_dismissed` / `_handle_promoted` / `_handle_inbox_status`. ~170L.
  - `inbox/branches/upgrade.py` — `_apply_upgrade_proposed`. Replaces the inline `_inbox/<type>/<slug>.proposed.md` rebuild at inbox.py:679-680 with a `paths._sibling_path` call (closes D2).
  - `inbox/state_io.py` — `SCHEMA_VERSION`, `StateSchemaError`, `atomic_write_state`, `load_state`. ~90L.
  - `inbox/types.py` — `ExtractedPage`, `ApplyResult`, `ExtractionIOError` dataclasses/errors.
  - `inbox/__init__.py` — shim re-exporting the full public surface.
- **`StateEntry.mark_written(run_id, new_hash)` method** added in `extract/scanner.py` — kills D4 (5 duplicate fresh-write blocks at inbox.py:501-510, 580-589, 596-604, 645-653, promote.py:66-74).
- **Consolidate `SCHEMA_VERSION` source of truth** — delete `scanner.py:39` default literal and import from `inbox/state_io.py` (kills D5).
- **`promote.py` migration**: switch from `_atomic_write`/`_file_hash` to the new public `atomic_write`/`content_hash`; replace its inline `_project_slug(file) + ".md"` constructions with calls to `paths` helpers where shapes match. Closes remaining D1/D2 cross-file dup.
- **Upstream consolidation**: `rule_activation.index._atomic_write_bytes` (20L, duplicate of `inbox/io.py::atomic_write` shape) — consider consolidating to `mnemo/core/io_utils.py` in this PR OR defer to a follow-up nit-PR. Pick one explicitly.
- **Gate**: PR 0 surface test + full 963-test suite must be green.

**PR F1 — Few-shot schema regression test** *(safety net; must land standalone)*
- Add `tests/unit/test_prompts_few_shot_schema.py`.
- For each few-shot constant (`_FEW_SHOT_FEEDBACK`, `_FEW_SHOT_USER`, `_FEW_SHOT_REFERENCE`), extract every `Output:` JSON blob via regex, then round-trip through **`mnemo.core.extract.__init__._parse_pages_from_response`** (the actual production filter) with the appropriate `default_type` and assert the returned page list is non-empty AND every page has non-empty `slug`, `body`, and `source_files` (the three fields that filter rejects on).
- **Why round-trip and not a Pydantic schema**: no Pydantic model exists for `ExtractedPage` coming from the LLM. `_parse_pages_from_response` IS the schema — it's the sanitization gate all LLM output goes through. Validating few-shot against it is the strongest guarantee the examples still calibrate the model correctly.
- **If F1 fails on current master**, we've surfaced latent drift — fix the examples in F1 before proceeding to F2.
- LOC: ~60, test-only.
- **Blocks**: PR F2.

**PR F2 — `prompts/` package split** *(templates + rendering, protected by F1)*
- Convert `prompts.py` → `prompts/` package:
  - `prompts/templates/system_feedback.py` (~90L)
  - `prompts/templates/system_simple.py` (~60L)
  - `prompts/templates/schema.py` — `_SCHEMA_EXAMPLE` (~45L)
  - `prompts/templates/few_shot_feedback.py` — `_FEW_SHOT_FEEDBACK` (~160L)
  - `prompts/templates/few_shot_simple.py` — `_FEW_SHOT_USER`, `_FEW_SHOT_REFERENCE` (~35L)
  - `prompts/encoding.py` — `_encode_file`, `_render_files`, `chunks_for` (~25L)
  - `prompts/vault_tags.py` — `_existing_tags_fragment` (~25L)
  - `prompts/render.py` — unified `build_consolidation_prompt(kind: ConsolidationKind, ...)` replacing three near-identical `build_*_prompt` functions; three thin wrappers preserve existing call-sites. ~90L.
  - `prompts/__init__.py` — shim.
- **Move `build_briefing_prompt`'s transcript flattener** to `core/briefing.py` or a new `core/transcript.py` — it's event-parsing logic, not prompt composition (SRP fix). `build_briefing_prompt` now accepts `transcript: str`.
- **Gate**: PR 0 surface test + PR F1 schema test + full suite green.

**PR H — `cli/` package** *(highest LOC, lowest semantic value — goes last)*
- Convert `cli.py` → `cli/` package:
  - `cli/__init__.py` (shim; re-exports `main`, `COMMANDS`, `_resolve_vault`. Verified monkeypatch surface: **2 test files with 10 occurrences** — not 15 as originally planned).
  - `cli/parser.py` — `_build_parser`, `command` decorator, `COMMANDS` registry.
  - `cli/runtime.py` — `main`, `_resolve_vault`, `_run_open`.
  - `cli/commands/*.py` — one module per command group (init, status, doctor, extract, briefing, recall, telemetry, statusline, misc).
  - `cli/commands/doctor_checks/*.py` — one module per concern (activation, fidelity, rules, reflex, misc).
  - `cli/_helpers/` — absorbs `cli_helpers.py` from PR A.
- **OCP fix**: `cmd_doctor` replaced with a `(name, fn) → (ok, warnings)` registry; adding a new check is a new module registration, not an edit to `cmd_doctor`.
- **Risk recalibrated**: medium (was "high"); verified monkeypatch count is 10 across 2 files, all on the single symbol `mnemo.cli._resolve_vault` — preserved by the shim.
- **Gate**: PR 0 surface test + full suite green.

## Risk matrix (revised)

| PR | Size | Blast radius | Test churn | Mitigation | Rollback |
|----|------|-------------|-----------|------------|----------|
| 0 | small | test-only | +1 new test file | surface test file adds no prod surface | revert |
| A | small | low | none | pure-function module | revert |
| B | small | low | none | pure-function module | revert |
| ~~C~~ | — | — | — | **dropped, folded into G** | — |
| D | small | low | 5 import rewrites | `session_state._FILENAME` matches counter's on-disk format | revert |
| E | small | low | 2 hard-coded assertions | auto-rebuild path already load-bearing | revert |
| F1 | small | none | +1 new test file | may catch latent drift in master → fix before F2 | revert |
| F2 | medium | medium | shim + templates move | F1 gates calibration, PR 0 gates shim surface | revert |
| G | large | medium | shim keeps 10+ callers; reflex patched in-PR | unified parse_block replaces _describe_*_error atomically | revert |
| H | large | medium | 10 monkeypatches on `mnemo.cli._resolve_vault` (2 files) | shim re-exports the three symbols needed | revert |
| I | large | medium-high | extract tests + promote.py migration | shim + D4 helper kills 5 dup blocks atomically | revert |

All 9 PRs (+ PR 0) are `git revert`-safe because each lands atomically and full-suite tests pass pre-merge. Runtime `ImportError` is the main post-merge failure mode; PR 0's surface test is the gate against it.

## Effort sizing
- Small (<200 LOC, ~1h review): **PR 0, A, B, D, E, F1**. 6 PRs.
- Medium (200–600 LOC, ~3h review): **F2**. 1 PR.
- Large (>600 LOC, ~half-day review): **G, H, I**. 3 PRs.

Total ballpark: ~2 full review days spread over ~1 calendar week if PRs don't collide on master.

## Execution sequencing
1. **PR 0** — surface test. Must merge before any Wave 3 PR.
2. **Wave 1** — PRs A + B in parallel (no dependencies between them).
3. **Wave 2** — PRs D + E sequential (E touches tests that Wave 1 didn't, so could also run parallel; keep sequential for simpler review).
4. **Wave 3** — **G → I → F1 → F2 → H**, sequential, each gated on: PR 0 green + full 963-test suite green + reviewer approval.

## Execution orchestration (HOW each PR gets done)

This plan is executed via the **claude-flow MCP swarm** (`mcp__claude-flow__*` tools), NOT by the main Claude instance editing files directly. Rationale: keeps the main context clean, parallelizes independent PRs, and isolates review cycles per PR.

### Pattern per PR

For **every** PR in this plan, the flow is:

1. **Main thread**: `mcp__claude-flow__swarm_init` with `topology: hierarchical, maxAgents: 3-4, strategy: specialized` (swarm per wave, not per PR — agents share context within a wave).
2. **Main thread**: `Agent(subagent_type: coder, run_in_background: true)` for each independent PR in the wave, with a self-contained prompt including: target files + LOC budget + acceptance criteria + rollback note + `gh pr create --base master` at the end.
3. **Main thread STOPS after dispatching** — per CLAUDE.md "After spawning, STOP — do NOT add more tool calls or check status. Trust agents to return."
4. **On agent completion**: review the diff, verify tests green locally via `pytest -q`, confirm the PR is stacked correctly (see stacked-PR pitfall below).
5. **Human merges** via GitHub UI after review.

### Wave-specific swarm shape

- **PR 0**: single `coder` agent. No parallelism needed.
- **Wave 1 (A, B)**: swarm of 2 `coder` agents dispatched in one message, background. Independent files, no collision risk. Main thread synthesizes when both return.
- **Wave 2 (D, E)**: sequential single-agent dispatches (E depends on D's CHANGELOG structure being established; also, D touches many test imports that conflict with anything else).
- **Wave 3 (G, I, F1, F2, H)**: strictly sequential single-agent dispatches. Each PR is large enough to warrant its own review cycle; parallelizing would create merge-conflict hell on `rule_activation.py` and `inbox.py`.

### Stacked-PR pitfall (must read before each dispatch)

This plan has been bitten **twice** in this project by the same failure mode (PRs #36 and #39):
- When `gh pr create --base <feature-branch>` is used and the feature-branch merges to master first, GitHub does **NOT** auto-retarget the stacked PR. Merging the stacked PR silently lands its commits on the stale branch, not master.
- **Mandatory protocol before every Wave 3 merge**: check `gh pr view <n> --json baseRefName` immediately before merge. If it's not `master`, retarget via GitHub UI (Edit → change base).
- **Preferred**: avoid stacking entirely. Each PR in this plan branches off master directly. Dependencies between PRs (e.g. PR I needing PR B's `inbox_paths.py`) are resolved by merging PR B first, then PR I branches off the new master.

### Prompt template for dispatching an agent

When dispatching an agent for any PR, the prompt MUST include:

```
- Target branch: <branch-name> off master (not stacked).
- Files to edit: <explicit list>.
- Acceptance criteria:
  - `pytest -q` passes (currently 963 green).
  - `git diff --stat` shows only expected files.
  - Public API surface test (tests/unit/test_public_api_surface.py) passes — mandatory for Wave 3.
- At the end: commit with Conventional Commit message, push, and `gh pr create --base master`.
- DO NOT merge. Open PR only.
- DO NOT run destructive git operations (reset --hard, force-push).
- Report back: PR URL + test count + any deviations from plan.
```

### Not using MCP swarm for

- Reviewing agent output (main thread does this).
- Merging PRs (human in the loop).
- Writing the surface test in PR 0 (it's small and the main thread can do it faster than spawning an agent).
- Rollback (if tests fail post-merge, main thread reverts).

## Out of scope
- Adding v0.9 reflex knobs (`maxHits`, `previewChars`, etc.) — feature PR, not refactor.
- `counter.py` → `session_state.py` file rename — only the shim goes; the real file was renamed in v0.8.
- Performance optimization beyond what the refactor naturally enables.
- **Modules not audited in this swarm pass** (deferred to a future audit if warranted): `src/mnemo/hooks/` (session_start.py 295L), `src/mnemo/core/config.py`, `src/mnemo/core/reflex/*.py`. Scope was bounded to the four biggest-offender files identified by the initial file-size census.
