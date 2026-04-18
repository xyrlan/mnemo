# Prompt Reflex — UserPromptSubmit inline rule injection

**Date:** 2026-04-18
**Status:** Design — awaiting user review
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
  All three coexist; dedupe is per-slug + TTL.
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
| `description`  | 2.0    | frontmatter `description:`            |
| `body`         | 1.0    | rule body (markdown, trimmed)         |

Rationale: `name` + `tags` are the curated signal (controlled vocabulary,
hand-tuned). `description` is the curated summary. `body` contains code
examples and prose that match too eagerly — it's the tiebreaker, not the
driver.

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

New file: `.mnemo/reflex-index.json` (separate from `rule-activation-index.json`
to keep concerns clean).

```json
{
  "version": 1,
  "generated_at": "2026-04-18T12:34:56Z",
  "scope": "project",
  "project": "mnemo",
  "avg_field_length": {
    "name": 3.2,
    "topic_tags": 2.1,
    "description": 12.4,
    "body": 184.7
  },
  "doc_count": 47,
  "postings": {
    "prisma": [
      {"slug": "use-prisma-mock", "tf": {"name": 0, "topic_tags": 1, "description": 2, "body": 6}}
    ],
    ...
  },
  "docs": {
    "use-prisma-mock": {
      "field_length": {"name": 3, "topic_tags": 1, "description": 11, "body": 240},
      "preview": "To mock prisma in tests, always use jest-mock-extended...",
      "stability": "stable",
      "universal": false,
      "project": "mnemo"
    },
    ...
  }
}
```

**Preview** is the first 300 chars of body (whitespace-normalized), stored
once at index-build time, so the hook never touches the source `.md` file.

**Index lifecycle** — piggyback on the existing rule-activation rebuild
triggers in `session_start.py`:

- When `reflex.enabled` is true OR `enforcement.enabled` OR `enrichment.enabled`
  OR `injection.enabled`, rebuild runs on every SessionStart.
- `mnemo extract` rebuilds after every extraction pass.
- Failure to rebuild = fail-open (hook finds no index → returns silently).

### Retrieval flow (`UserPromptSubmit` hook)

```
1. Parse stdin payload.
2. If reflex.enabled=false → return (exit 0, empty stdout).
3. If circuit breaker tripped (errors.should_run(vault) false) → return.
4. Read session state from .mnemo/mnemo-session-state.json:
   - If `date` != today, wipe `injected_cache`.
5. Tokenize prompt, strip stopwords, drop fenced code blocks first.
   - Pre-gate: if remaining tokens < 3 distinct non-stopwords → return.
   - Cap query at 200 tokens (protects against pasted stack traces).
6. Load reflex-index.json.
   - If missing/corrupt → return (fail-open).
7. Score all docs via BM25F; sort desc.
8. Triple-gate:
   - (a) Term-overlap: at least 2 distinct query tokens (post-stopword)
         must appear in the top-1 doc's combined indexed content (union of
         name + topic_tags + description + body token sets).
   - (b) Relative gap: s[0] >= 1.5 * s[1] (or s[1] == 0).
   - (c) Absolute floor: s[0] >= 2.0.
   If any fails → return (silence).
9. Build hit list:
   - Always include top-1.
   - Include top-2 iff (a) it also passes term-overlap, (b) s[1] >= 2.0,
     (c) it is not already in injected_cache within TTL.
10. Dedupe against injected_cache:
    - For each candidate slug, skip if cache[slug] + TTL > now.
    - If no candidates survive → return (silence).
11. Emit hookSpecificOutput.additionalContext with the canonical format.
12. Update injected_cache[slug] = now for each emitted slug.
13. Append one reflex-log.jsonl line per emitted slug.
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

### Dedupe: session-state.json

Extend `mcp-call-counter.json` into `.mnemo/mnemo-session-state.json`:

```json
{
  "count": 14,
  "date": "2026-04-18",
  "injected_cache": {
    "use-prisma-mock": 1713456000,
    "react-cache-versioning": 1713457800
  }
}
```

**Migration**: on first read with old shape, upgrade in place
(`injected_cache: {}`). `mnemo-session-state.json` is a **rename** of
`mcp-call-counter.json`:

- Install-time migration writes the new file and removes the old one.
- Status line + any existing reader of the old name must be updated in the
  same PR (already owned by mnemo; no external consumers).

**TTL**: `reflex.dedupeTtlMinutes` (default 120). Any hook that emits a slug
writes its `now()` into the cache. Any hook about to emit a slug reads the
cache and skips if `cache[slug] + TTL > now`.

**Scope of dedupe**: cross-hook between **Reflex and PreToolUse enrichment**
only. Both hooks emit rule bodies keyed by slug, so a shared slug cache
prevents the "same body twice" problem.

**SessionStart is explicitly excluded** from the cache: it injects a topic
*list*, not rule bodies, and runs once per session regardless. Including it
would require tracking a different granularity (topics vs slugs) with no
user-visible benefit.

**Required cross-module change**: `src/mnemo/hooks/pre_tool_use.py`
(`_emit_enrich`) must be updated in the same PR to:
1. Read `mnemo-session-state.json` before emitting.
2. Filter `hits` against `injected_cache` + TTL.
3. Write emitted slugs back into the cache.

This is a small, clearly-scoped extension of the existing enrichment path —
not a rewrite.

**Rotation**: daily, driven by the existing `date` mismatch logic. New day
→ wipe `injected_cache` alongside `count` reset.

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
    "bm25f": {
      "k1": 1.5,
      "b": 0.75,
      "fieldWeights": {
        "name": 3.0,
        "topic_tags": 3.0,
        "description": 2.0,
        "body": 1.0
      }
    }
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

Registered in `~/.claude/settings.json` by `mnemo init`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {"matcher": "*", "hooks": [{"type": "command", "command": "python -m mnemo.hooks.user_prompt_submit"}]}
    ]
  }
}
```

**Uninstall**: `mnemo uninstall` removes the entry cleanly, same pattern as
SessionStart/SessionEnd/PreToolUse.

## Observability

- `.mnemo/reflex-log.jsonl` — per-emission log:
  ```json
  {"ts": "2026-04-18T12:34:56Z", "session_id": "abc", "project": "mnemo",
   "prompt_hash": "sha256:...", "prompt_tokens": 8,
   "emitted": ["use-prisma-mock"], "scores": [4.7, 1.1],
   "silence_reason": null}
  ```
  When silenced, `emitted: []` and `silence_reason` is one of
  `below_min_tokens` | `term_overlap_fail` | `relative_gap_fail` |
  `absolute_floor_fail` | `deduped` | `index_missing`.

  **Privacy**: only `prompt_hash` is logged, not the raw prompt. `prompt_tokens`
  is the count after stopword removal. Users who want to debug live can
  opt-in to `reflex.debug.logRawPrompt: true` to record the full prompt in
  the log line — off by default so the log is safe to share.

- **Status line**: new segment `3⚡ today` (count of emissions today) next to
  the existing `7↓ today` MCP call counter. Uses the same session-state file,
  so no new disk reads.

- **`mnemo doctor`** new checks:
  - `reflex-index-stale` — index older than last extraction.
  - `reflex-no-hits-7d` — vault has >=20 rules but zero emissions in 7 days
    (threshold too tight).
  - `reflex-noisy-7d` — >30% of prompts trigger emission (threshold too
    loose; tune up).
  - `reflex-broken-frontmatter` — rule has empty `description` + no
    `topic_tags` + name < 3 tokens (effectively invisible to Reflex).

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

**Unit tests** (`tests/core/reflex/`):
- `test_tokenizer.py` — stopwords, kebab/snake preservation, fenced code
  stripping, 200-token cap.
- `test_bm25f.py` — known corpus + known queries, pinned expected scores
  (golden regression).
- `test_gates.py` — each of the triple-gate failures produces silence; all
  three passing produces emission.
- `test_index_build.py` — from a synthetic vault of 5 rules, build index,
  assert shape + previews.
- `test_dedupe.py` — cache hit skips emission, TTL expiry restores
  emission, day rollover wipes cache.

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
