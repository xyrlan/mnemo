# Prompt Reflex — UserPromptSubmit inline rule injection

**Date:** 2026-04-18
**Status:** Design — approved after engineering review (2026-04-18)
**Target release:** v0.8.0 (next minor after v0.7.0)

## One-liner

When the user submits a prompt, mnemo runs a BM25F match between the prompt text
and the vault's rule corpus for the current project's scope, and injects the
body preview of the top 1–2 rules inline via `UserPromptSubmit` hook
`additionalContext` — **but only when a triple-gate confidence test passes**.
Default behaviour is silence.

## Motivation

Today the mnemo loop has three injection points, each reactive to Claude's
*actions*:

1. `SessionStart` — lists topic tags, one-line instruction to Claude.
2. `PreToolUse` enrichment — fires when Claude is about to `Edit`/`Write`.
3. `PreToolUse` enforcement — fires when Claude is about to run `Bash`.

None of them fire on **user intent**. The user types a prompt about mocking
Prisma; Claude has to (a) remember `mnemo://v1` told it topics exist, (b)
pick `prisma`, (c) call `list_rules_by_topic`, (d) call `read_mnemo_rule`,
(e) apply. Four round-trips before a single line of code, and the round-trip
*is the enemy* — Claude frequently skips it ("I think I can solve this
without the rule") and hallucinates.

Prompt Reflex collapses the chain: the rule body is already in Claude's
context **before** it sees the prompt. Zero MCP calls, zero Claude reasoning
about retrieval.

## Non-goals

- **Not** a semantic search. No embeddings, no vector DB. mnemo is stdlib-only.
- **Not** a replacement for SessionStart injection or PreToolUse enrichment.
  All three coexist. Reflex and enrichment share a per-slug session-lifetime
  dedupe cache; SessionStart is excluded (different granularity — topic
  lists vs rule bodies).
- **Not** cross-project. Reflex reads the same scope as v0.7 default:
  `local + universal` rules owned by the current project.
- **Not** a training loop. Reflex does not rewrite rules based on usage.

## Architecture

### Corpus & index (BM25F)

Each rule indexed as a multi-field document with weights:

| Field          | Weight | Source                                |
|----------------|--------|---------------------------------------|
| `name`         | 3.0    | frontmatter `name:`                   |
| `topic_tags`   | 3.0    | frontmatter `tags:` (topic tags only) |
| `aliases`      | 2.5    | frontmatter `aliases:` (new in v0.8)  |
| `description`  | 2.0    | frontmatter `description:`            |
| `body`         | 1.0    | rule body (markdown, trimmed)         |

Rationale: `name` + `tags` are the curated signal (controlled vocabulary,
hand-tuned). `aliases` carries bilingual / synonym bridges for pure lexical
BM25 (see "Aliases field" below). `description` is the curated summary.
`body` contains code examples and prose that match too eagerly — it's the
tiebreaker, not the driver.

### Aliases field (recall bridge for bilingual/synonym gap)

BM25 is pure lexical — no stemming, no synonyms, no cross-lingual
matching. For codebases where developers prompt in Portuguese against
English-tagged rules ("como mockar o **banco**" vs rule about
"mock the **database**"), recall silently collapses to zero.

Fix: optional `aliases: [...]` frontmatter field, indexed as a fourth
BM25F field with weight 2.5 (between `topic_tags` and `description`).

```yaml
---
name: use-prisma-mock
description: Always use jest-mock-extended to mock Prisma in tests
aliases:
  - banco
  - database
  - db
  - prisma
  - orm
---
```

**Who writes aliases**:
- Extraction LLM emits `aliases:` for any rule whose `description` or
  `body` contains bilingual terms or common domain synonyms. Requires
  updating **all three** system prompts in `src/mnemo/core/extract/prompts.py`:
  `FEEDBACK_SYSTEM_PROMPT`, `USER_SYSTEM_PROMPT`, and
  `REFERENCE_SYSTEM_PROMPT`. Long architecture references written in
  Portuguese must also match EN prompts; user-profile rules benefit the
  least but stay consistent for schema simplicity. The per-prompt JSON
  schema each references must also carry the new `aliases: string[]`
  field.
- Users can hand-edit aliases on their own rules; content-addressed
  merge already protects edits.

**Degradation**: rules without `aliases:` still score normally via the
other 4 fields. The feature is strictly additive — zero regressions on
existing vault content.

**Cost**: ~30 LOC in the extract pipeline prompt + 1 field path in the
tokenizer + 1 row in the index `avg_field_length` map.

**Tokenizer** (stdlib):
- Lowercase.
- Split on `[^a-z0-9_-]+` (keeps kebab-case and underscores as single
  tokens, so `package-management` and `path_globs` survive).
- Stopword removal: a conservative English+Portuguese list (~60 words) kept
  in `src/mnemo/core/reflex/stopwords.py`.
- No stemming (adds ambiguity for code terms; `mock` / `mocking` already
  both appear in real rule bodies).

**BM25F parameters** (starting values, configurable):
- `k1 = 1.5`
- `b = 0.75`
- Per-field `b` set globally; no per-field length normalization (premature).

### Index artifact

New file: `.mnemo/reflex-index.json` — **vault-wide**, mirroring the structure
of the existing `rule-activation-index.json`. One index covers all projects;
project filtering happens at query time via each doc's `projects[]` + `universal`
fields. This avoids the orchestration nightmare of per-project indexes when
the user switches cwds mid-session.

```json
{
  "schema_version": 1,
  "generated_at": "2026-04-18T12:34:56Z",
  "avg_field_length": {
    "name": 3.2,
    "topic_tags": 2.1,
    "aliases": 3.8,
    "description": 12.4,
    "body": 184.7
  },
  "doc_count": 47,
  "postings": {
    "prisma": [
      {"slug": "use-prisma-mock", "tf": {"name": 0, "topic_tags": 1, "aliases": 1, "description": 2, "body": 6}}
    ],
    ...
  },
  "docs": {
    "use-prisma-mock": {
      "field_length": {"name": 3, "topic_tags": 1, "aliases": 5, "description": 11, "body": 240},
      "preview": "To mock prisma in tests, always use jest-mock-extended...",
      "stability": "stable",
      "projects": ["mnemo"],
      "universal": false
    },
    ...
  }
}
```

**Consumer-visibility gate** (non-negotiable): the index builder walks
`shared/{feedback,user,reference}/*.md` and calls `is_consumer_visible` on
every candidate — same gate as `rule_activation.build_index`
(`src/mnemo/core/rule_activation.py:342`). Pages under `_inbox/`, tagged
`needs-review`, or with `stability: evolving` are **skipped**. This keeps
Reflex in lockstep with the v0.4 filter-parity contract: if the HOME
dashboard can't see a rule, Reflex can't inject it either.

**Preview** reuses `mnemo.core.rule_activation._body_preview(text, max_chars=300)`
— the existing helper strips frontmatter and truncates on whitespace
boundaries, avoiding mid-word cuts. Do **not** reimplement. If Reflex lives
outside `rule_activation`'s module, promote `_body_preview` to a public
helper (e.g. `core/text_utils.py`) rather than duplicating.

**Per-doc `projects` + `universal`** are derived from the rule's
`sources[]` frontmatter using `projects_for_rule` + `_is_universal` helpers
already in `rule_activation.py`. Reuse these — no new scope-derivation
logic.

**Index lifecycle** — piggyback on the existing rule-activation rebuild
triggers in `session_start.py:148-153`:

- When `reflex.enabled` is true OR `enforcement.enabled` OR `enrichment.enabled`
  OR `injection.enabled`, rebuild runs on every SessionStart. Call
  `reflex.build_index(vault)` + `reflex.write_index(vault, idx)` right
  after `rule_activation.write_index`.
- `mnemo extract` rebuilds after every extraction pass (same extension
  point).
- Failure to rebuild = fail-open (hook finds no index → returns silently).

### Retrieval flow (`UserPromptSubmit` hook)

```
1. Parse stdin payload.
2. If reflex.enabled=false → return (exit 0, empty stdout).
3. If circuit breaker tripped (errors.should_run(vault) false) → return.
4. Resolve current project:
   - cwd = payload.get("cwd") or str(Path.cwd())
   - project = resolve_agent(cwd).name
   (Same pattern as pre_tool_use.py:63 — do not diverge.)
5. Read session state from .mnemo/mnemo-session-state.json:
   - If `date` != today, wipe `injected_cache` and `session_emissions`.
   - GC: remove entries from `session_emissions` whose `started_at` is
     older than 24h (handles crashed sessions where SessionEnd never ran).
   - If `session_emissions[sid].reflex_count >= reflex.maxEmissionsPerSession`
     → return (log `silence_reason: "session_cap_reached"`).
6. Tokenize prompt, strip stopwords, drop fenced code blocks first.
   - Pre-gate: if remaining tokens < 3 distinct non-stopwords → return.
   - Cap query at 200 tokens (protects against pasted stack traces).
7. Load reflex-index.json.
   - If missing/corrupt → return (fail-open).
8. Build candidate slug set (project-scoped):
   - candidates = union of
       {slug for slug,doc in index.docs if project in doc.projects}
       {slug for slug,doc in index.docs if doc.universal}
   - Dedup; this is exactly `local + universal` per the v0.7 scope model.
9. Score only candidates via BM25F; sort desc.
10. Triple-gate:
    - (a) Term-overlap: at least 2 distinct query tokens (post-stopword)
          must appear in the top-1 doc's combined indexed content (union of
          name + topic_tags + aliases + description + body token sets).
    - (b) Relative gap: s[0] >= 1.5 * s[1] (or s[1] == 0).
    - (c) Absolute floor: s[0] >= 2.0.
    If any fails → return (silence).
11. Build final hit list, deduped in one pass:
    - Start with top-1. Always kept (gates above guarantee it's confident).
    - Append top-2 iff (a) it passes term-overlap, (b) s[1] >= 2.0.
    - Filter the resulting list against `injected_cache`: any slug already
      present (session-lifetime scope) is dropped. No separate second
      dedupe pass.
    - If the filtered list is empty → return (silence).
12. Emit hookSpecificOutput.additionalContext with the canonical format.
13. Update injected_cache[slug] = now for each emitted slug.
14. Increment session_emissions[sid].reflex_count by number of slugs emitted.
15. Append one reflex-log.jsonl line per emitted slug, via the
    access_log.record pattern (rotate_if_needed + _sanitize for free).
```

### Payload format

```
mnemo reflex context:
• [[use-prisma-mock]]: To mock prisma in tests, always use jest-mock-extended... (call read_mnemo_rule if you need the full file).
• [[react-cache-versioning]]: Key-remount pattern requires bumping version on... (call read_mnemo_rule if you need the full file).
```

- Max 2 bullets.
- Each bullet: preview truncated at 300 chars, with " (call read_mnemo_rule
  if you need the full file)." always appended (ensures Claude knows the
  escape hatch).
- Double-wikilink `[[slug]]` format matches enrichment payload style for
  visual consistency.

### Dedupe & session budget: extending `mcp-call-counter.json` in place

**Decision: extend, do not rename.** The file stays at
`.mnemo/mcp-call-counter.json`. The `{date, count}` contract that
`statusline.py:143` and `mcp/server.py:170` depend on is preserved
verbatim. Two new top-level keys are added:

```json
{
  "count": 14,
  "date": "2026-04-18",
  "injected_cache": {
    "use-prisma-mock": 1713456000,
    "react-cache-versioning": 1713457800
  },
  "session_emissions": {
    "sid-abc123": {
      "started_at": 1713455000,
      "reflex_count": 3,
      "enrich_count": 1
    }
  }
}
```

**Module rename (code-hygiene)**: `src/mnemo/core/mcp/counter.py` is renamed
to `src/mnemo/core/mcp/session_state.py`. The old module path stays as a
thin compat shim re-exporting `increment` and `read_today` so statusline
and server.py continue to import from `mnemo.core.mcp.counter` without
churn. The shim can be removed in v0.9. Name-vs-content consistency: future
readers see `session_state` and understand the module owns more than a
counter.

**Critical `increment()` fix**: the current
`counter.py:increment()` rebuilds the entire JSON as `{date, count}` on
each call (line 41: `data = {"date": today, "count": 0}`). This would
**silently wipe `injected_cache` and `session_emissions` on every MCP
call**. The new `session_state.increment()` MUST read-modify-write,
preserving unknown keys:

```python
# Correct shape:
data = _load_or_init(path)  # preserves injected_cache / session_emissions
if data.get("date") != today:
    # Daily rollover wipes the session-state keys; count too.
    data = {"date": today, "count": 0, "injected_cache": {}, "session_emissions": {}}
data["count"] = int(data.get("count", 0)) + 1
_atomic_write(path, data)
```

Any new helper (`read_injected_cache`, `add_injection`, `bump_emission`,
`gc_old_sessions`) applies the same read-modify-write discipline.

**Migration at runtime**: on first read of an old-shape file (no
`injected_cache` / `session_emissions`), auto-upgrade in memory by seeding
those keys to `{}`. No install-time migration script needed.

**TTL — session-lifetime default** (revised for long-running sessions):

- Default dedupe behaviour: **a slug injected once stays suppressed for the
  entire session**, not just a wall-clock window. The cache key
  `injected_cache[slug]` is consulted; if it exists at all and belongs to
  the current session, skip.
- `reflex.dedupeTtlMinutes` (default `120`) acts as a **floor**, not a
  ceiling: if a session runs longer than 2h, dedupe still holds — the
  entry does not "expire back" into re-injection territory within the same
  session.
- The cache is wiped on three triggers:
  1. Daily rollover (`date` mismatch, same as today).
  2. `SessionEnd` hook (new behaviour — clears `session_emissions[sid]` and
     removes `injected_cache` entries last-touched by that sid).
  3. **24h session GC** (new): on every UserPromptSubmit / PreToolUse read
     of session-state, entries in `session_emissions` whose `started_at`
     is older than 24h are removed. Handles crashed sessions (SIGKILL,
     hardware reset) where SessionEnd never ran. Prevents unbounded JSON
     growth over months.

This reflects the corrected understanding that `additionalContext` from
`UserPromptSubmit` persists in the transcript. Once Claude has seen the
rule body, re-injecting it adds context-bloat with no recall benefit.

**Per-session hard cap** (new safety rail):

- `reflex.maxEmissionsPerSession` (default `10`). Once
  `session_emissions[sid].reflex_count >= cap`, Reflex returns silence for
  the remainder of the session regardless of score.
- `enrichment.maxEmissionsPerSession` (default `15`, slightly higher
  because path-based triggering is more targeted). Same mechanic.
- Combined caps are independent: Reflex hitting its cap does not silence
  Enrichment, and vice versa.
- When a cap is hit, the hook logs a single `silence_reason:
  "session_cap_reached"` line to the respective log and then stays quiet
  until SessionEnd clears the counter.

**Worst-case accounting**: 10 Reflex × 2 bullets × 300 chars = 6 KB ≈
~1.5K tokens max accumulated over any session, regardless of length.
Adding the enrichment cap (15 × 1 bullet × 300) = 4.5 KB ≈ ~1.1K tokens.
Session-wide ceiling for mnemo-injected content: **~2.6K tokens** — below
the compaction trigger for any reasonable session length.

**Scope of dedupe**: cross-hook between **Reflex and PreToolUse enrichment**
only. Both hooks emit rule bodies keyed by slug, so a shared slug cache
prevents the "same body twice" problem.

**SessionStart is explicitly excluded** from the cache: it injects a topic
*list*, not rule bodies, and runs once per session regardless. Including it
would require tracking a different granularity (topics vs slugs) with no
user-visible benefit.

**Required cross-module changes** (same PR):

1. `src/mnemo/core/mcp/counter.py` → renamed to
   `src/mnemo/core/mcp/session_state.py`:
   - Old module path becomes a thin shim re-exporting `increment` and
     `read_today` (preserves imports from `statusline.py:143` and
     `mcp/server.py:25,170`). Shim removable in v0.9.
   - `increment` rewritten as read-modify-write that preserves unknown
     top-level keys (see "Critical `increment()` fix" above).
   - New public helpers: `read_injected_cache`, `add_injection`,
     `bump_emission`, `gc_old_sessions` — all applying the same
     read-modify-write discipline.
2. `src/mnemo/hooks/pre_tool_use.py` (`_emit_enrich`):
   - Read session-state before emitting.
   - Enforce `enrichment.maxEmissionsPerSession` cap first — if
     `session_emissions[sid].enrich_count >= cap`, return silence.
   - Filter `hits` against `injected_cache` (session-lifetime scope).
   - Write emitted slugs back into the cache and increment
     `session_emissions[sid].enrich_count`.
3. `src/mnemo/hooks/session_end.py`:
   - After the existing session-clear logic, remove `session_emissions[sid]`
     and evict `injected_cache` entries last-touched by that sid.
   - Fail-silent; state corruption here never breaks SessionEnd.
4. `src/mnemo/install/settings.py`:
   - Add `"UserPromptSubmit"` entry to `HOOK_DEFINITIONS` (matcher=None).
   - No other changes — inject/uninject logic iterates the dict and
     handles the rest.
5. `src/mnemo/core/extract/prompts.py`:
   - Add `aliases:` guidance block to all three system prompts
     (`FEEDBACK_SYSTEM_PROMPT`, `USER_SYSTEM_PROMPT`,
     `REFERENCE_SYSTEM_PROMPT`) and the JSON schema each references.
6. `src/mnemo/statusline.py`:
   - Add `3⚡` segment (no "today" suffix) pulled from
     `session_state.read_today_emissions()` (new helper).

These are small, clearly-scoped extensions of existing hook / install /
statusline code — not rewrites.

**Rotation**: daily, driven by the existing `date` mismatch logic. New day
→ wipe `injected_cache` and `session_emissions` alongside `count` reset.

## Config

New block in `~/mnemo/mnemo.config.json`:

```json
{
  "reflex": {
    "enabled": true,
    "maxHits": 2,
    "previewChars": 300,
    "dedupeTtlMinutes": 120,
    "thresholds": {
      "termOverlapMin": 2,
      "relativeGap": 1.5,
      "absoluteFloor": 2.0,
      "minQueryTokens": 3
    },
    "maxEmissionsPerSession": 10,
    "bm25f": {
      "k1": 1.5,
      "b": 0.75,
      "fieldWeights": {
        "name": 3.0,
        "topic_tags": 3.0,
        "aliases": 2.5,
        "description": 2.0,
        "body": 1.0
      }
    }
  },
  "enrichment": {
    "maxEmissionsPerSession": 15
  }
}
```

**Default `reflex.enabled`** is release-gated:

- **v0.8.0-alpha**: `false` (dogfood in mnemo repo; tune thresholds from
  log observations).
- **v0.8.0 stable**: `true` (matches v0.6+ dogfood philosophy — ship the
  working product, not an inert scaffold).

The triple-gate makes the feature inert on low-confidence prompts, which is
most of them in the first week; token cost stays near zero until the vault
reaches a size where Reflex has ground-truth to stand on.

**Kill switch**: `reflex.enabled: false`. The hook is absolutely fail-open
— any error short-circuits to exit 0, empty stdout.

## Hooks wiring

New hook module: `src/mnemo/hooks/user_prompt_submit.py`.

**Registration**: do NOT hand-roll settings.json. Add an entry to
`HOOK_DEFINITIONS` in `src/mnemo/install/settings.py:44`:

```python
HOOK_DEFINITIONS: dict[str, dict[str, Any]] = {
    "SessionStart": {"module": "session_start", "matcher": None, "async": False},
    "PreToolUse":   {"module": "pre_tool_use",  "matcher": "Bash|Edit|Write|MultiEdit", "async": False},
    "SessionEnd":   {"module": "session_end",   "matcher": None, "async": False},
    # new:
    "UserPromptSubmit": {"module": "user_prompt_submit", "matcher": None, "async": False},
}
```

`matcher=None` means no matcher key in the JSON (Claude Code applies the
hook to every event of that type). This matches the SessionStart /
SessionEnd convention — do not set `matcher: "*"`.

**Uninstall comes for free**: `_strip_mnemo_entries` uses the `mnemo.hooks.`
tag embedded in the command string, and `_do_inject`/`_do_uninject` iterate
`HOOK_DEFINITIONS` automatically. No changes needed to uninstall code.

## Observability

- `.mnemo/reflex-log.jsonl` — per-emission log, written via the
  `mnemo.core.mcp.access_log.record` pattern (reuse that writer verbatim or
  factor out a shared helper):
  - `rotate_if_needed(log_path, cfg.reflex.log.maxBytes or 1_048_576)`
    before every append — same rotation infra as denial/enrichment logs.
  - `_sanitize(entry)` applies 1024-char truncation per string value,
    guarding against rogue long fields (free secret-spill mitigation if
    `logRawPrompt` is on).
  Log entry shape:
  ```json
  {"ts": "2026-04-18T12:34:56Z", "session_id": "abc", "project": "mnemo",
   "prompt_hash": "sha256:3a7f0b9c1d2e", "prompt_tokens": 8,
   "emitted": ["use-prisma-mock"], "scores": [4.7, 1.1],
   "silence_reason": null}
  ```
  `prompt_hash` uses the first 12 hex chars of sha256 (collision rate
  1/16^12 is sufficient for debugging). When silenced, `emitted: []` and
  `silence_reason` is one of `below_min_tokens` | `term_overlap_fail` |
  `relative_gap_fail` | `absolute_floor_fail` | `deduped` |
  `session_cap_reached` | `index_missing`.

  **Privacy**: only the truncated hash is logged, not the raw prompt.
  `prompt_tokens` is the count after stopword removal. Users who want to
  debug live can opt in to `reflex.debug.logRawPrompt: true` to record the
  full prompt — off by default, and `_sanitize` caps any value at 1024
  chars on write anyway.

- **Status line**: new segment `3⚡` next to the existing
  `mnemo · 3 topics · 7↓` format. No "today" suffix — matches the existing
  style (`7↓`, `3⛔ rules`, `2 blocks`, `1💡 active`). Uses the same
  session-state file, so no new disk reads.

- **`mnemo doctor`** new checks:
  - `reflex-index-stale` — index older than last extraction.
  - `reflex-no-hits-7d` — vault has >=20 rules but zero emissions in 7 days
    (threshold too tight).
  - `reflex-noisy-7d` — >30% of prompts trigger emission (threshold too
    loose; tune up).
  - `reflex-broken-frontmatter` — rule has empty `description` + no
    `topic_tags` + name < 3 tokens (effectively invisible to Reflex).
  - `reflex-session-cap-hit` — >20% of sessions in the last 7d hit
    `maxEmissionsPerSession` (cap too low, or noise leaked past gates).
  - `reflex-bilingual-gap` — vault has >=3 rules with `description`
    containing non-ASCII chars but no `aliases:` field (hint to
    extraction-LLM prompt tuning or manual authoring).

- **`mnemo status`** shows: reflex emissions today, last 3 emissions
  (slug + score), current thresholds, dedupe cache size.

## Error handling

The `UserPromptSubmit` hook runs on **every prompt**. Fail-open is
non-negotiable:

- Any exception at any stage → exit 0, empty stdout.
- Malformed `reflex-index.json` → treat as missing.
- Corrupt `mnemo-session-state.json` → reset to empty shape, do not block.
- Index older than 24h → still use it (don't force silence on stale
  indexes; SessionStart rebuilds, so this is a transient state).

**Error telemetry**: errors logged to `.mnemo/errors.jsonl` via
`errors.log_error(vault, "user_prompt_submit.<stage>", exc)` — same pattern
as other hooks. Circuit breaker (`errors.should_run`) gates the hook on
repeated failures.

## Testing

> **Test surface scales with the revisions**: after the design-review
> corrections, the test plan grew from ~13 to ~20 files. Implementers
> should budget accordingly; the golden regression set alone is load-
> bearing for the "silence vs emission" contract.

**Unit tests** (`tests/core/reflex/`):
- `test_tokenizer.py` — stopwords, kebab/snake preservation, fenced code
  stripping, 200-token cap.
- `test_bm25f.py` — known corpus + known queries, pinned expected scores
  (golden regression); includes aliases-field contribution test.
- `test_gates.py` — each of the triple-gate failures produces silence; all
  three passing produces emission.
- `test_index_build.py` — from a synthetic vault of 5 rules, build index,
  assert shape + previews + aliases indexing.
- `test_dedupe.py` — session-lifetime dedupe holds across turns; day
  rollover wipes cache; SessionEnd wipes session_emissions + sid-scoped
  cache entries.
- `test_session_cap.py` — after N emissions in one session, further
  Reflex calls return silence with `session_cap_reached` reason;
  enrichment cap behaves independently.
- `test_aliases_bilingual.py` — PT prompt ("mockar o banco") matches
  EN-described rule with `aliases: [banco]`; fails without aliases.
  Matrix: three rule types (feedback / user / reference) × with-aliases
  and without-aliases, to prove all three extraction prompts emit the
  field.

**New unit tests from design-review corrections**:
- `test_project_filter.py` (C1) — vault with rules from project A and
  project B; prompt that matches both lexically. From cwd of project A,
  only project-A-local + universal rules are candidates; project-B-local
  rule never surfaces regardless of score.
- `test_counter_preserves_unknown_keys.py` (C2) — seed
  `mcp-call-counter.json` with `count`, `injected_cache`,
  `session_emissions`. Call `mcp.counter.increment` (the shim). Assert
  `count` bumps AND `injected_cache` / `session_emissions` survive verbatim.
- `test_vault_wide_index_shape.py` (C3) — build index across a 3-project
  synthetic vault; every doc has `projects: list[str]` and
  `universal: bool`; no top-level `project` / `scope` fields remain.
- `test_consumer_visible_gate.py` (C4) — vault includes a rule under
  `shared/_inbox/feedback/`, a rule tagged `needs-review`, and a rule
  with `stability: evolving`. Build index, assert none of the three
  appear in `docs`.
- `test_session_emissions_gc.py` (W7) — seed `session_emissions` with two
  entries, one with `started_at` 25h ago, one 30min ago. After next read
  via session-state helper, only the fresh entry remains.

**Integration tests** (`tests/integration/reflex/`):
- `test_hook_silent.py` — prompt "ok continua" → exit 0, empty stdout.
- `test_hook_emits.py` — prompt "mockar prisma no teste" against a vault
  containing `use-prisma-mock` → emits expected payload.
- `test_hook_deduped.py` — second identical prompt in same session → silent.
- `test_hook_failopen.py` — corrupt index → exit 0, empty stdout, error
  logged.
- `test_install_uninstall.py` — `mnemo init` adds hook entry;
  `mnemo uninstall` removes it; idempotent.

**Golden test** (`tests/integration/test_reflex_regression.py`): a fixed
vault of 20 hand-crafted rules + a set of 30 prompts, each with its
expected-slug-or-silence outcome. This is the Reflex equivalent of the
v0.5.x retrieval golden set (mentioned in project memory).

**Performance budget**: UserPromptSubmit must complete in <100ms on a vault
with 500 rules. Benchmarked in `tests/integration/test_reflex_perf.py`.

## Rollout

- **v0.8.0-alpha (dogfood, 1 week)**: ship with `reflex.enabled: false`
  default. Dogfood in mnemo repo itself. Tune thresholds from
  reflex-log.jsonl observations.
- **v0.8.0 (release)**: flip default to `true`. Changelog calls out the new
  hook + config block.
- **v0.8.1+**: consider adding `reflex.scope` override (allow `vault` for
  cross-project recall) if demand surfaces.

## Open questions (none load-bearing)

- Should the preview strip leading frontmatter/headers (e.g., `## Why:`
  blocks)? Initial call: no — the preview is the first 300 chars verbatim,
  and rule authors already put the signal up top.
- Should Reflex emit a marker in the statusLine when the hot-path took
  >50ms? Nice-to-have; v0.8.1.
- Should `reflex.debug.logRawPrompt` redact obvious secrets (API keys,
  bearer tokens) before logging? Defer to `v0.8.1`; initial scope is
  off-by-default.

## Success criteria

- After 2 weeks of dogfood: Reflex fires on >=10% of prompts with
  non-trivial content (>=3 non-stopword tokens), and <=1% of those fires
  are deemed false positives by the user (self-reported or inferred from
  Claude ignoring the rule).
- Latency P50 <30ms, P95 <100ms on vault of 500 rules.
- Zero session-blocking incidents attributable to the hook.
- `mnemo doctor` reports green on `reflex-*` checks.
