# Cold-start backfill — design

**Date:** 2026-08-01
**Status:** approved, not implemented
**Workstream:** 1 of 3 on the road to v1.0 (backfill → extraction precision → injection receipts)

## Problem

A new user installs mnemo and sees nothing for weeks.

Measured on the maintainer's own vault after ~4 months of daily dogfood:

```
reflex-log: 3380 prompts, 237 injected  →  7.0% emit rate

silence reasons:
  relative_gap_fail   35.3%
  index_missing       24.5%   ← mnemo had nothing to say
  below_min_tokens    13.8%
  absolute_floor_fail 10.4%
  deduped              7.4%
```

A vault with four months of accumulated rules injects on 7% of prompts. A fresh
vault injects on 0% — every prompt hits `index_missing` until enough sessions
have accrued to extract from. The product's entire value proposition is invisible
during the window when a new user decides whether to keep it.

Meanwhile the raw material already exists on disk. Claude Code retains full
session transcripts at `~/.claude/projects/<slug>/*.jsonl` — 117 top-level
transcripts totalling 177 MB on the maintainer's machine (875 files / 366 MB
counting nested worktree directories), averaging ~81 user turns each. mnemo
ignores all of it.

Backfill converts that history into a populated vault at install time.

## Constraints

- **Extraction costs tokens.** The pipeline shells out to the `claude` CLI. An
  uncapped sweep of 875 transcripts is a large, surprising spend on a user's
  account. Cost must be bounded by default and disclosed before any large run.
- **Backfilled memory is lower-confidence than live memory.** Live memory files
  are written by Claude during the session that produced the lesson. Backfilled
  ones are an LLM reconstructing intent from an old transcript. The vault already
  archives 87% of extracted rules (925 archived vs 105 alive); reconstruction at
  scale could amplify that.
- **The pipeline downstream of memory files works.** extract → inbox → promote →
  index → reflex is mature and tested. Backfill must not modify it.

## Architecture

One new unit. Everything downstream is untouched.

```
~/.claude/projects/<slug>/*.jsonl
        │
        ▼
   core/backfill/harvest.py        ← NEW: transcript → memory files
        │
        ▼
bots/<repo>/memory/*.md            (stamped metadata.origin: backfill)
        │
        ▼
  extract → _inbox → promote → index → reflex     (UNCHANGED)
```

### Why this seam

Extraction reads `bots/*/memory/*.md`. Those files are written by Claude during
a live session, via the memory instructions mnemo injects into the system prompt
— there is no code path that produces them from a transcript. Old sessions never
carried those instructions, so backfill cannot replay capture; it must synthesise
memory files.

That synthesis is the *only* missing piece. Inserting it at the memory-file seam
means the entire downstream pipeline, including its quality gating, applies to
backfilled material for free.

### Precedent to mirror

`core/briefing.py::generate_session_briefing(jsonl_path, agent, cfg)` already
accepts an arbitrary transcript path and does exactly the shape of work harvest
needs: `_load_jsonl_events` → `flatten_transcript_events` → `llm.call` → write
markdown. Harvest mirrors it — same model resolution
(`extraction.model`, default `claude-haiku-4-5`), same
`extraction.subprocessTimeout`, same atomic write.

It also mirrors that function's signal threshold: `generate_session_briefing`
returns `None` when a transcript contains zero file mutations
(Edit/Write/MultiEdit/NotebookEdit tool_use). Harvest applies the same gate, so
pure-conversation sessions are skipped at zero LLM cost.

### Rejected alternatives

**Replay old transcripts through the live capture path.** Not possible — capture
depends on instructions injected into a running session.

**Bypass memory files: transcript → LLM → `shared/` directly.** Duplicates the
extraction pipeline, and skips the inbox/promote quality gate exactly where
confidence is lowest.

**Salience pre-filter before the LLM call** (keep only correction-shaped turns:
negations, "instead", "always/never", redirects). Would cut cost 5–10x, but the
benefit is unmeasured and the install-time path is only ~20 sessions. Deferred to
the extraction-precision workstream, which will have real numbers from this one.

## Components

| Unit | Responsibility | Depends on |
|---|---|---|
| `core/backfill/harvest.py` | one session → N memory files | `llm`, `transcript`, `paths` |
| `core/backfill/discover.py` | map `~/.claude/projects/` slugs → repos, rank by mtime | `agent.resolve_canonical_agent` |
| `core/backfill/ledger.py` | `.mnemo/backfill-state.json`, skip-if-done | — |
| `core/extract/prompts/harvest.py` | the harvest prompt | — |
| `cli/commands/backfill.py` | `mnemo backfill` | all of the above |
| `hooks/session_start.py` | fire the capped first run, once | `backfill` |

The split isolates the only unit that needs LLM mocking. `discover` and `ledger`
are pure functions over the filesystem and test without any LLM at all.

## Data flow

### Entry point 1 — install-time, automatic, capped

On the first `SessionStart` after install (guarded by
`backfill.autoOnFirstSession` and a ledger marker so it fires exactly once):

1. Discover transcripts for the **current repo only**.
2. Rank by mtime, take the most recent `backfill.installCap` (default 20).
3. Drop sessions already in the ledger, and sessions below
   `backfill.minFileMutations`.
4. Harvest each into `bots/<repo>/memory/`.
5. Print a summary of what was produced.

Bounded by `installCap` and the existing `extraction.costSoftCap`.

**The hook must not block the prompt path.** Harvest makes one LLM call per
session; running it inline would stall session start for minutes. `session_start`
therefore spawns it detached, exactly as `session_end` already does for briefings
and extraction (`_spawn_detached_briefing` → `subprocess.Popen` of
`mnemo briefing …`). The hook writes the ledger marker and returns immediately;
`mnemo backfill --install-run` does the work in the background, and its summary
surfaces on the *next* session start.

### Entry point 2 — explicit sweep

```
mnemo backfill [--all] [--dry-run] [--project X] [--limit N]
```

Prints an estimate — session count and projected token spend — and requires
confirmation before spending. `--dry-run` writes nothing and reports what would
be produced.

### Idempotency

`.mnemo/backfill-state.json` is keyed by session id + transcript file hash. An
interrupted run resumes; a rerun is a no-op; a transcript that changed on disk is
re-harvested. `--all` is therefore incremental — it picks up only what the capped
install run did not cover.

## The `origin: backfill` gate

Harvested memory files carry `metadata.origin: backfill` in frontmatter.

In `core/extract/inbox/branches/auto_promoted.py`, a candidate whose source files
are **all** backfill-origin is denied auto-promotion and routed to `shared/_inbox`
for review. A candidate with any live-authored source promotes normally.

Two payoffs:

1. A day-1 user's `shared/` cannot be flooded by reconstruction. The rules they
   see first are ones they chose to keep.
2. Rule quality becomes measurable by origin — backfill-derived vs live-derived
   survival rates. That measurement is the input to the extraction-precision
   workstream that follows this one.

## Config

Added to `DEFAULTS`:

```json
"backfill": {
  "enabled": true,
  "installCap": 20,
  "minFileMutations": 1,
  "autoOnFirstSession": true
}
```

`docs/configuration.md` is generated from `DEFAULTS` and covered by
`test_docs_accuracy.py`, so the table updates with the code.

## Error handling

Fire-and-forget at the hook, matching the briefing path:

- A per-session failure logs to `~/mnemo/.errors.log` and the run continues. One
  malformed transcript never aborts a sweep.
- The ledger records failures with an attempt count. Three strikes and a session
  is permanently skipped.
- An LLM rate-limit (detected by the existing `llm._is_rate_limit`) stops the run
  cleanly and prints a resume hint, rather than burning retries.
- Extraction downstream is already covered by the circuit breaker.

## Testing

- **`discover`** — fixture `.claude/projects/` tree; slug→repo mapping, worktree
  `.git` pointer resolution, mtime ranking, `--project` filtering.
- **`ledger`** — idempotency, resume after interruption, re-harvest on hash
  change, three-strike permanent skip.
- **`harvest`** — stubbed `llm.call`; frontmatter shape, `origin: backfill` stamp,
  zero-mutation skip, atomic write.
- **`auto_promoted`** — an all-backfill candidate lands in `_inbox`, not
  `shared/`; a mixed-origin candidate still promotes.
- **CLI** — `--dry-run` writes nothing; the estimate is printed before any spend;
  `installCap` and `--limit` are respected.
- **`session_start`** — the automatic run fires exactly once and is skipped when
  `autoOnFirstSession` is false.
- **e2e** (opt-in, alongside the two existing skipped e2e tests) — a real
  transcript through the full pipeline to an indexed rule.

## Out of scope

- Salience pre-filtering (deferred; see Rejected alternatives).
- Importing existing `CLAUDE.md` / `AGENTS.md` files. Separate, cheaper input
  path; worth doing, but it is not transcript backfill and should not ride along.
- `/mnemo:why` injection receipts and extraction-precision tuning — workstreams 2
  and 3, each with its own spec.
