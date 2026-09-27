# Session Handoff — last-briefing injection at SessionStart

**Date:** 2026-04-20
**Status:** Design — pending engineering review
**Target release:** v0.10.0 (next minor after v0.9.x refactor stabilization)

## One-liner

When a Claude Code session starts in a project, mnemo reads the most recent briefing belonging to that project (across worktree siblings) and injects its full body into the SessionStart prompt via the existing `mnemo://v1` envelope. Claude wakes up knowing exactly where the previous session left off, with zero MCP round-trips. The same change adds `usage` (input/output token counts) to every `mcp-access-log.jsonl` entry that triggers an LLM call, so `mnemo telemetry` can finally report real cost.

## Motivation

Briefings already capture per-session shift-handoff context (decisions made, dead ends, next steps), but today they are write-only as far as Claude is concerned. To use them, the user has to manually open the markdown file in Obsidian, or wait for the extraction step to consolidate them into rules — which loses the high-fidelity per-session detail that makes a briefing useful as a handoff.

The injection point that already exists for rules (SessionStart hook → `mnemo://v1` envelope) is the natural place to surface the latest briefing. No new MCP tool, no new Claude-side reasoning, no extra round-trip. Claude opens a fresh session and the first thing it reads is "you were refactoring `auth.ts`, the test failed at line 42, you stopped before fixing the regex".

A secondary motivation: today we have no way to estimate mnemo's actual token cost vs savings. Briefing and extraction LLM calls happen invisibly. Injection envelopes add tokens to every session prompt. Adding a `usage` field to the access log unlocks the telemetry path needed to make ROI claims defensible.

## Non-goals

- **Not** a search / browse tool for arbitrary briefings. No `list_briefings` / `read_briefing` MCP tools. If you want to read a specific historical briefing, open it in Obsidian (`mnemo open`).
- **Not** multi-briefing aggregation. Only the single most recent briefing is injected. No "last N", no "last 24 hours", no summary.
- **Not** content transformation. The briefing body is injected verbatim; no LLM-based re-summarization at read time.
- **Not** a freshness gate. Even a 6-month-old briefing is injected if it's the most recent one for the project. Stale handoffs are still better than nothing, and freshness heuristics rot.
- **Not** a synchronous SessionEnd briefing generator. Briefing generation stays async/detached as today; the race window is accepted explicitly (see "Race condition" below).

## Architecture

### Project resolution (canonical agent)

`agent.resolve_agent(cwd)` today returns the basename of the directory containing `.git`. For a worktree at `~/github/mnemo-feature-x`, `.git` is a **file** (not a directory) containing `gitdir: <path>`, and the basename is the worktree's own name — yielding `agent="mnemo-feature-x"`, distinct from the main repo's `agent="mnemo"`.

For session handoff to work across worktrees, both the **writer** (briefing generation in the previous SessionEnd) and the **reader** (injection in the new SessionStart) must agree on the same agent name. Approach: introduce a new helper `agent.resolve_canonical_agent(cwd)` that returns the project's canonical name regardless of worktree:

- If `.git` is a directory → canonical root is the current repo root, `name = basename(repo_root)`.
- If `.git` is a file → parse the `gitdir: <path>` line, resolve `<path>/commondir` (or walk `<path>` upward to the `worktrees/` parent) → canonical repo root → `name = basename(canonical_root)`.
- If no `.git` is found → fall back to `resolve_agent(cwd)` (current behavior).

Both **briefing write** (`hooks/session_end.py:_maybe_schedule_briefing`) and **briefing read** (new SessionStart injection path) call `resolve_canonical_agent`. Result:

- Main repo writes & reads `bots/mnemo/briefings/sessions/`.
- Any worktree of the same repo also writes & reads `bots/mnemo/briefings/sessions/`.
- All worktrees + main share one briefing pool, naturally.

**Migration of existing data.** Vaults that already have orphan `bots/<worktree-name>/briefings/sessions/` directories (written before this change) are left in place; the new code does not move or delete them. A one-shot CLI command `mnemo migrate-worktree-briefings` is in scope as part of the implementation plan: it scans `bots/*/briefings/sessions/` for each agent dir whose name resolves to a worktree of an existing canonical repo and moves the briefings into the canonical agent's directory, prompting before destructive moves.

**Other mnemo paths (extraction, memory pages, rules) are NOT changed.** They continue to use `resolve_agent` and segregate by worktree. The canonical resolution is scoped to briefings only, where pool-sharing is the desired semantics.

### Briefing selection

The "last briefing" for the canonical agent is the file in `bots/<canonical>/briefings/sessions/*.md` with the **most recent frontmatter `date`**, breaking ties by `session_id` lexicographic order (deterministic). File mtime is a fallback when frontmatter parsing fails.

Body is injected **whole**, no truncation, no token cap. Empirically briefings range 500–5000 tokens; this is one fixed cost paid per session start, comparable to today's rule-list injection footprint.

### Injection envelope

The existing SessionStart hook emits an `additionalContext` markdown block carrying the `mnemo://v1` envelope (topic list + scope marker). Extend the envelope with a new bracketed section:

```
mnemo://v1 project=mnemo
local: [automation, workflow, ...]
Call list_rules_by_topic(topic) then read_mnemo_rule(slug) BEFORE writing code.
Use scope="project" for local+universal, scope="local-only" to exclude universal.

[last-briefing session=<session_id> date=<YYYY-MM-DD> duration_minutes=<N>]
<full briefing body, verbatim>
[/last-briefing]
```

Position within the SessionStart `additionalContext` block: **after** the topic list / scope instructions, as the last section of the envelope. The framing line `[last-briefing session=… date=… duration_minutes=…]` gives Claude a structured anchor it can reference ("based on the last-briefing for session abc123…").

When no briefing exists for the canonical agent, the bracketed section is omitted entirely (not emitted as empty). Existing envelope content is unaffected.

### Race condition handling

Briefings are generated by a detached subprocess fired from the previous `SessionEnd` hook. The subprocess can take 30–60s to complete (LLM call + atomic write). If the user opens a new session within that window, the new session's SessionStart will read the **second-most-recent** briefing (the previous one), not the just-finished session.

**Decision: best-effort read. No waiting, no lock check.** Reasons:

1. The race is rare (sub-minute session turnaround).
2. When it triggers, the cost is small: the previous-previous briefing is still useful context.
3. Adding a wait (lock-file timeout) makes SessionStart latency-variable, which compounds across every session even when there's no race.
4. Sync briefing generation in SessionEnd would block the terminal for 30–60s — the current async design exists exactly to avoid this.

Documented behavior: "if you close and immediately re-open a session, the new session may not see the freshly-finished briefing yet — wait ~30s if you need it." A future enhancement could surface the pending state in `mnemo status`.

### Token usage instrumentation

Independent of the handoff feature, this spec includes adding token accounting to every LLM call mnemo makes. Without this, we cannot answer "what does mnemo cost?" or "what does this new injection cost?".

Changes:

- **`llm.call()`** (single chokepoint for all LLM invocations): always capture `response.usage.input_tokens` and `response.usage.output_tokens` from the Anthropic SDK response. Return them as part of `LLMResponse` (which today carries only `text`).
- **Call sites** (`briefing.generate_session_briefing`, `extract.*` consolidation, anywhere else that calls `llm.call`): on success, append a structured entry to `mcp-access-log.jsonl` with shape:
  ```json
  {
    "timestamp": "2026-04-20T15:30:00Z",
    "tool": "llm.call",
    "purpose": "briefing" | "consolidation:feedback" | "consolidation:user" | ...,
    "model": "claude-haiku-4-5",
    "project": "mnemo",
    "agent": "mnemo",
    "usage": {"input_tokens": 12345, "output_tokens": 678},
    "elapsed_ms": 2345.6
  }
  ```
- **`mnemo telemetry`** subcommand: aggregate the new `tool: "llm.call"` entries by `purpose`, `model`, and `project`. Display total input/output token counts and an estimated USD cost using a simple per-model price table (`claude-haiku-4-5: $1/MTok in, $5/MTok out`, etc., kept in a small `pricing.py` module that can be updated as Anthropic price changes).
- The injection envelope size (rule-list bytes + briefing body bytes) is also worth logging once per SessionStart as a separate `tool: "session_start.inject"` entry, so we can quantify the always-on cost of injection vs the on-demand cost of LLM calls.

This is additive: no existing log consumer breaks, no schema bump (new optional fields on a fresh tool name).

### Sequence

1. New Claude Code session opens in `~/github/mnemo` (or any worktree of the same repo).
2. SessionStart hook fires (`hooks/session_start.py`).
3. Hook calls `agent.resolve_canonical_agent(cwd)` → `mnemo` (same for main repo and any worktree of it).
4. Hook reads existing rule-activation index, builds the topic list as today.
5. Hook scans `bots/mnemo/briefings/sessions/*.md`, picks the file with max frontmatter `date`, tie-break by `session_id`, fallback to mtime.
6. Hook reads that briefing, parses frontmatter, extracts body. On any failure → omits the section, logs warning.
7. Hook composes the `mnemo://v1` envelope with the new `[last-briefing …]` section appended as the last block.
8. Hook emits `additionalContext` to Claude.
9. Hook appends a `tool: "session_start.inject"` entry to the access log with envelope byte size and whether a briefing was included.

## Configuration

New keys in `mnemo.config.json` (additive, defaults preserve current behavior):

```json
{
  "briefings": {
    "enabled": true,
    "injectLastOnSessionStart": true
  }
}
```

- `injectLastOnSessionStart` (default `true`): master switch for the feature. When `false`, SessionStart envelope is identical to today.
- No CLI override flag at first cut. If the user wants to disable per session, they edit the config — same as every other mnemo toggle today.

## Error handling

- **No briefings on disk** → omit the `[last-briefing]` section silently. Not an error.
- **Briefing frontmatter unparseable** → fall back to file mtime for ordering. Log a warning to `.errors.log` once per session.
- **Briefing body unreadable (OS error)** → omit the section, log to `.errors.log`. Do not abort the rest of the SessionStart envelope.
- **`.git` parsing fails** → fall back to `agent.resolve_agent(cwd).name` (current behavior). Worktree unification simply doesn't apply.
- **All exceptions in injection path are caught and logged** with `where="session_start.briefing_inject"`. SessionStart must never break Claude's session start due to a briefing-injection bug — the rule-list path must continue to work.

## Testing

- Unit: `resolve_canonical_agent` for `.git` dir (main), `.git` file (worktree), missing `.git` (fallback). Cover malformed `.git` file (bad `gitdir:` line) → fall back gracefully.
- Unit: briefing picker — most-recent-by-date selection, tie-break by session_id, fallback to mtime when frontmatter date missing or unparseable.
- Unit: envelope composition — verify the `[last-briefing …]` section is appended last, omitted entirely when no briefing exists, formatted with the framing line including session_id/date/duration.
- Unit: error paths — unparseable frontmatter, OSError on read, all degrade silently to "no briefing section" plus an `.errors.log` entry.
- Integration: end-to-end SessionStart hook with a real `bots/<agent>/briefings/sessions/*.md` fixture, asserting the emitted markdown contains the briefing body verbatim.
- Integration: worktree shared-pool behavior — fixture with `.git` file pointing to a parent repo, briefing written under the canonical agent dir, SessionStart in the worktree picks it up.
- Migration: `mnemo migrate-worktree-briefings` moves orphan briefings into the canonical dir, dry-run mode lists what would move, no-op when there's nothing to migrate.
- Telemetry: `usage` field round-trip — `llm.call()` returns input/output token counts, the call site logs them under `tool: "llm.call"`, `mnemo telemetry` aggregates them by purpose/model/project and renders an estimated USD cost.

Target: ≥95% coverage on the new module(s). Existing 1024 tests must continue to pass.

## Out of scope

- Multi-briefing injection (last N).
- Freshness window / time-decay.
- Synchronous SessionEnd briefing.
- New MCP `list_briefings` / `read_briefing` tools.
- Cross-worktree storage unification (write-time).
- Briefing summarization at read time.
- Per-session opt-out CLI flag.

## Open questions

1. Should the handoff injection respect the user-set `scope` (local / local-only / vault) the way rule injection does, or is briefing always per-project? **Tentative answer: always per-project; briefings are inherently session-local artifacts.**
2. Should `mnemo telemetry` show injection cost as a recurring fixed cost (per session) separately from LLM call cost (per extraction/briefing event)? **Tentative answer: yes, two separate aggregations.**
3. Pricing table — keep in `mnemo` or fetch dynamically? **Tentative answer: hard-code; update with each Anthropic price change. mnemo is stdlib-only.**
