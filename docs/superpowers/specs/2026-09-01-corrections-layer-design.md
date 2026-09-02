# mnemo as the corrections layer — design

**Date:** 2026-09-01
**Status:** approved (post-1.0.0 audit)
**Scope:** four sequential workstreams, one PR each: A extraction with evidence, B trust defaults, C the five-minute loop, D honest distribution. Out of scope: Codex/Cursor adapters, a `mnemo dev` CLI regroup, changes to PreToolUse enforcement.

## Why

The post-1.0 audit (45-rule stratified sample, competitive survey, fresh-eyes onboarding) found:

- 1648 live rules; 98% have `source_count = 1`; 16% were ever injected by the reflex.
- In `shared/feedback` (85% of the vault) only 31% are genuine user corrections; 38% are textbook best practice the model already knows; the rest are project facts or session narrative filed under the wrong type.
- The rule with the highest `source_count` (8) is the extractor's own system-prompt guidance echoed back by the LLM. Three of four checked source briefings never mention it.
- Near-duplicate families of 5–10 rules persist because each extraction mints a fresh slug instead of reinforcing an existing rule, so `source_count` never accrues and universal promotion "almost never happens" (own comment in `core/universal_candidates.py`).
- README claims "zero network calls" while the autopilot runs `gh issue create` and `gh pr create` by default. First visible value takes days and 20+ unconsented Haiku calls.

Root cause of the quality findings: the extractor never sees the user's words. Its input is Tier 1 memory pages (Claude Code's own auto-memory) plus session briefings, whose "Decisions made" section is an LLM summarising its own session. A correction cannot be evidenced from that input, so nothing distinguishes "the user corrected Claude" from "Claude explained something".

The category is validated (Claude Code auto-memory is default since 2026-02; claude-mem passed 90k stars after it shipped) and its open complaints are exactly what mnemo can own: corrections ignored or lost, bad memories cascading, no veto or expiry, token burn from observer-style tools. Per-prompt relevance injection is mnemo's real differentiator and nobody else does it.

## Workstream A — extraction with evidence

### A1. Corrections section in the briefing

`session_end` already spawns one briefing LLM call over the flattened transcript. That call gains a second input and a second output.

- **Input.** `core/transcript.py` gains `user_turns(events) -> list[str]`: the text of every `user`-role event, in order, excluding machine-written user turns (task notifications, hook context, slash-command stdout, `tool_result`-only messages — reuse the machine-turn exclusion in `core/mcp/recall_sessions.py`, moved into `core/transcript.py` so both callers share it). The briefing prompt appends a block `## User turns (verbatim)` with each turn numbered, truncated to 600 characters.
- **Output.** `BRIEFING_SYSTEM_PROMPT` gains an eighth section `## Corrections`, placed after `## Decisions made`. Each item: `- "<verbatim quote from a user turn>" → <one-line rule the quote establishes>`. The prompt states that a correction is the user telling Claude to stop, change, prefer, or never/always do something; explanations by Claude and decisions Claude made alone do not qualify; omit the section when there is none.
- **Verification.** `core/corrections.py` parses the section and keeps only items whose quote is a substring of some user turn after whitespace and case normalisation and stripping of surrounding quotes. Rejected items are dropped before the briefing is written; the count of rejected items is logged via `errors.log_error` under `briefing.corrections_rejected` so `mnemo doctor` can surface fabrication rates.
- **Storage.** Verified items are rendered into the briefing markdown under `## Corrections` and additionally into the briefing frontmatter as `corrections: [{quote, rule}]` so downstream code does not re-parse prose.

### A2. Promotion gate

`ExtractedPage` gains `evidence: dict | None` = `{"quote": str, "source": "<vault-relative briefing path>"}` and `confidence: "verified" | "inferred"`.

- The feedback system prompt requires each page to cite the exact quote it was built from and the source file that carries it, and says pages with no supporting user quote must be emitted with `type: reference`.
- The schema example adds `"evidence": {"quote": "...", "source": "bots/<agent>/briefings/sessions/<id>.md"} | null`.
- After parsing, `core/extract/evidence.py::verify(page, vault_root)` opens the cited source, reads its `corrections:` frontmatter, and checks the quote matches one entry (same normalisation as A1). Match → `confidence: verified`. No match, or no evidence → the page is coerced to `type: reference`, `confidence: inferred`, and routed to `shared/_inbox/reference/` regardless of how many sources it has. Only verified feedback pages take the `auto_promoted` path into `shared/feedback/`.
- Rendering writes `evidence:` and `confidence:` into the page frontmatter. `core/reflex/index.py` indexes the evidence quote as a fifth BM25F field with weight 2.5 (the user's own words are the best lexical bridge to their next prompt). `rule_activation` carries both fields through unchanged.
- `user` and `project` types are untouched by the gate: `project` pages are the user's own memory files; `user` pages are rare and already reviewed.

### A3. Reinforce, do not mint

Two layers, both in the feedback/user/reference paths.

- **Prompt-time.** `build_consolidation_prompt` appends `## Existing rules (reuse the slug when the rule is the same)` listing `slug — name` for every live and inbox page of the same type whose `projects` intersect the chunk's agents, capped at 80 entries ordered by `source_count` desc. The system prompt instructs the model to reuse an existing slug when the new material supports the same rule and to only mint a slug for a genuinely new rule.
- **Apply-time.** `inbox/dedup.py` gains `_detect_similar_existing(page, state, vault_root)`: weighted Jaccard over stemmed tokens of `name` (×3), `description` (×2), `body` (×1) against every existing page of the same type, no source-set requirement, threshold 0.5. The existing `_detect_drift_slug` and `_detect_stem_collision` remain and run first. On match the page's slug is redirected to the existing one and `union_with_prior_sources` accumulates the new source, so `source_count` grows and `universal_promotion` can fire.
- Tests must include a mutation guard: with the threshold set to 1.01 the family stays split; at 0.5 it merges.

### A4. Guards

- **Prompt echo.** `core/extract/guards.py::is_prompt_echo(page)` rejects a page whose name, description, or body contains any of a fixed phrase list drawn from the extractor prompts: `blocking intent`, `enforce block`, `stability field`, `tier 2 page`, `aliases field`, `activates_on`, `deny_pattern`, `sacred directory`, `existing vault tags`. Rejections are counted in `ExtractionSummary.echo_rejected` and logged.
- **PII redaction.** `core/redact.py::redact(text) -> tuple[str, int]` replaces e-mail addresses, bearer-style tokens (`sk-…`, `ghp_…`, `xox[abp]-…`, `AKIA…`), and hex strings of 32+ characters with `[redacted]`. Applied to every page body and description at render time, and to the evidence quote. Count of replacements is stored in `ExtractionSummary.redactions`.

### A5. `mnemo reclassify` (one-time, opt-in)

Reads every live page in `shared/feedback/`, batches ten per Haiku call together with the `corrections:` frontmatter and `## Decisions made` section of each cited source briefing that exists, and asks for one verdict per rule:

- `keep` — a user quote in the sources supports it; emit `evidence` and it becomes `verified`.
- `demote` — real project knowledge, no user correction → move to `shared/reference/`, `confidence: inferred`.
- `merge:<slug>` — same rule as another page in the batch or in the existing-rules list → union sources into the target, archive this one.
- `archive` — generic best practice, session narrative, or prompt echo.

`--dry-run` prints the plan as a table and the call estimate; the real run writes `shared/_archive/reclassify-<run_id>/manifest.json` recording every move so `mnemo reclassify --undo <run_id>` restores byte-for-byte. Index rebuild runs at the end. Verdicts are validated: `merge` targets must exist; a `keep` with no verifiable quote is downgraded to `demote`. Expected cost on the maintainer's vault: about 150 calls.

## Workstream B — trust defaults

- **B1.** New config key `autopilot.network.enabled`, default `false`. When false: `insights/digest.py` skips `gh issue create` and writes the digest to `.mnemo/proposals/` only; `selffix` applies cures in place and skips branch/PR; `outcome_poller` is a no-op. `mnemo autopilot status` shows `network: off`. Local jobs (indexes, sweep, calibration, tuner) are unchanged.
- **B2.** `backfill.autoOnFirstSession` default becomes `false`. On the first session of a vault with no backfill ledger, `session_start` injects one line: `[mnemo] first run: N past sessions for this repo can be learned with \`mnemo backfill\` (opt-in, about N Haiku calls).` N comes from `core/backfill/scanner` counting eligible transcripts for the cwd. The line is emitted once (ledger flag `firstRunNoticeShown`).
- **B3.** Learned ledger and announcement. `inbox.apply_pages` appends `{run_id, slug, type, projects, confidence}` to `.mnemo/learned.jsonl` for every fresh auto-promoted page. `session_start` reads entries for the current project newer than `.mnemo/announced.json[project]`, injects up to five lines under `[mnemo learned since your last session]` in the form `• slug — name (verified from: "<quote>") · veto: mnemo disable-rule slug`, then advances the marker. `disable-rule` already exists and is promoted from advanced to public help.
- **B4.** Enforcement is left as is: one rule uses it, and the hook is fail-open.

## Workstream C — the five-minute loop

- **C1.** `mnemo learn` (public CLI, slash `/mnemo:learn`). Steps: resolve the newest `~/.claude/projects/<encoded cwd>/*.jsonl` for the current directory (fall back to `--session <id>`); run the briefing for it with A1 corrections; run extraction with `force=True` restricted to that briefing's scanner key; rebuild the reflex and activation indexes; print `learned:` lines showing slug, name, and the verified quote, or `nothing new: no corrections found in this session` with a hint on how to phrase a correction. Bypasses the extraction debounce and the extraction lock wait (fails fast with a message if another extraction holds the lock).
- **C2.** The `minIntervalMinutes` debounce applies only when `extraction-state.json` records a prior run; the first extraction of a vault runs on the first SessionEnd.
- **C3.** Docs: a "Five minutes" section in `docs/getting-started.md` with the exact sequence to demonstrate a correction being re-injected, and a GIF storyboard the maintainer records.

## Workstream D — honest distribution

- **D1.** README rewrite of the top, "Check it worked", "Commands", "Autopilot", and "Privacy" sections. Tagline: "Claude Code forgets your corrections. mnemo doesn't." The Monday/Thursday example becomes same-project first, then a sentence on cross-project universal promotion and what it needs. New section "How it compares" with three rows: CLAUDE.md, Claude Code auto memory, claude-mem. Privacy: "no network unless you turn `autopilot.network` on; LLM calls go through your own `claude` CLI". Jargon removed from user-facing text: reflex → per-prompt recall, `bots/` explained as per-project capture, no tiers.
- **D2.** One reproducible number block under the tagline, sourced from commands in the box: reflex emit rate and tokens per injection, `mnemo recall` primacy@5 over its case count, and the big-bucket improvement table from PR #105. `mnemo status` prints the same numbers so the README and the tool agree.
- **D3.** Slash commands reduced to `status`, `why`, `doctor`, `learn`; `SLASH_COMMANDS` in `install/settings.py` is the source of truth and the plugin manifest regenerates from it. Removed commands stay available as CLI subcommands.
- **D4.** `docs/configuration.md` documents `autopilot.network.enabled`, the new backfill default, `evidence`/`confidence` frontmatter, and `mnemo learn` / `mnemo reclassify`.

## Testing

Every workstream is TDD: failing test first, then the change. Required guards:

- A1: fabricated quote rejected; quote with different whitespace/case accepted; machine-written user turns excluded from the numbered list.
- A2: unverified feedback page lands in `_inbox/reference/` even with three sources; verified page lands in `shared/feedback/` with `confidence: verified`.
- A3: family of three near-duplicate pages collapses to one slug with `source_count = 3`; threshold mutation guard.
- A4: page containing `enforce block` rejected; e-mail in body replaced; count reported.
- A5: dry-run writes nothing; undo restores the exact tree; merge target missing → verdict downgraded.
- B: with default config no `gh` subprocess is spawned in digest, selffix, or poller (spy on `subprocess.run`/`Popen`); first-run notice emitted once; announcement capped at five and marker advanced.
- C: `learn` with no corrections prints the hint and writes nothing; with one correction the rule exists and the reflex index contains its slug; debounce skipped on first run only.
- D: manifest regeneration stays byte-stable in CI; `mnemo status` numbers parse.

## Rollout

Four PRs in order A → B → C → D, each green on the full suite before the next starts. `mnemo reclassify --dry-run` runs on the maintainer's vault after A merges; the real run after inspection. Release 1.1.0 after D, following the release procedure documented in `.github/workflows/release.yml` (manual `workflow_dispatch` build must be green before the tag is pushed).
