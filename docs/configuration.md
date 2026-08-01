# Configuration

Config lives at `~/mnemo/mnemo.config.json` — or `<vault-root>/mnemo.config.json`
wherever your vault is. In `--project` installs that's `<cwd>/.mnemo/`.

Every key has a default, so the file only needs the ones you're changing.
Unknown keys are preserved on write.

Defaults are defined in `src/mnemo/core/config.py` — that file is the source of
truth if this page ever falls behind it.

## The switches that matter

Everything below is on out of the box. These are the five you'd realistically
turn off:

| Key | Default | What turning it off does |
|---|---|---|
| `extraction.auto.enabled` | `true` | No background extraction — rules only get written when you run `mnemo extract` yourself |
| `briefings.enabled` | `true` | No per-session briefings. Extraction gets much thinner input |
| `injection.enabled` | `true` | Claude is no longer told about the MCP tools at session start (the tools still work) |
| `reflex.enabled` | `true` | No automatic rule injection on prompts |
| `backfill.autoOnFirstSession` | `true` | No automatic one-time sweep of your old transcripts on the first session. `mnemo backfill` still works by hand |

```json
{
  "extraction": { "auto": { "enabled": false } },
  "reflex": { "enabled": false }
}
```

## Full reference

### Top level

| Key | Default | Meaning |
|---|---|---|
| `vaultRoot` | `~/mnemo` | Where the vault lives. `~` is expanded |
| `capture.sessionStartEnd` | `true` | Log 🟢/🔴 markers at session boundaries |
| `agent.strategy` | `git-root` | How project names are derived. Only `git-root` exists today |
| `agent.overrides` | `{}` | Reserved |

### `extraction` — turning session trails into rules

| Key | Default | Meaning |
|---|---|---|
| `extraction.model` | `claude-haiku-4-5` | Model used for extraction |
| `extraction.chunkSize` | `10` | Source files per LLM call |
| `extraction.preferAPI` | `false` | Use API-key auth instead of your Claude subscription |
| `extraction.subprocessTimeout` | `60` | Seconds before an extraction call is abandoned |
| `extraction.costSoftCap` | `null` | Warn past this spend, in dollars. `null` = no cap |
| `extraction.auto.enabled` | `true` | Run extraction automatically at `SessionEnd` |
| `extraction.auto.minNewMemories` | `1` | New source files required before a run |
| `extraction.auto.minIntervalMinutes` | `60` | Minimum gap between automatic runs |

### `backfill` — filling a new vault from old transcripts

Claude Code keeps every session it ever ran at
`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`. Backfill sends those
through the LLM and writes memory files, so a vault installed today has
material from months ago. See [getting-started.md](getting-started.md#backfill)
for what it does and how to review the result.

| Key | Default | Meaning |
|---|---|---|
| `backfill.enabled` | `true` | Master switch. `false` makes `mnemo backfill` a no-op that says so, and stops the automatic sweep |
| `backfill.installCap` | `20` | Most sessions the automatic first-run sweep will harvest — newest first, current repo only. Ignored by an explicit `mnemo backfill`, which uses `--limit` |
| `backfill.minFileMutations` | `1` | Sessions that touched fewer files than this are skipped without an LLM call |
| `backfill.autoOnFirstSession` | `true` | Run that capped sweep once, in the background, on the first session after install |

The automatic sweep runs **once per vault**, and only after it finishes: a
sweep that dies because the `claude` CLI is unreachable leaves the one-shot
unspent, and the next session tries again.

To upgrade without it ever running:

```json
{ "backfill": { "autoOnFirstSession": false } }
```

### `briefings` — the per-session summary

| Key | Default | Meaning |
|---|---|---|
| `briefings.enabled` | `true` | Write a briefing at `SessionEnd` |
| `briefings.injectLastOnSessionStart` | `true` | Hand the previous briefing to the next session as context |

### `injection` — the session-start topic list

| Key | Default | Meaning |
|---|---|---|
| `injection.enabled` | `true` | Emit the topic list into `additionalContext` |
| `injection.maxTopicsPerScope` | `15` | Cap on topics listed per scope |
| `injection.telemetry.enabled` | `true` | Record injection events for `mnemo telemetry` |
| `injection.telemetry.log.maxBytes` | `1048576` | Rotate the log past this size |

### `reflex` — per-prompt rule retrieval

The single most relevant rule, injected inline before Claude answers, and only
when it clears all three thresholds.

| Key | Default | Meaning |
|---|---|---|
| `reflex.enabled` | `true` | Run retrieval on every prompt |
| `reflex.maxEmissionsPerSession` | `10` | Stop injecting after this many hits in one session |
| `reflex.thresholds.termOverlapMin` | `2` | Query/rule terms that must overlap |
| `reflex.thresholds.relativeGap` | `1.5` | How far the top hit must beat the runner-up |
| `reflex.thresholds.absoluteFloor` | `2.0` | Minimum score to inject at all |
| `reflex.thresholds.minQueryTokens` | `3` | Prompts shorter than this are skipped |
| `reflex.bm25f.k1`, `reflex.bm25f.b` | `1.5`, `0.75` | Standard BM25 parameters |
| `reflex.bm25f.fieldWeights.*` | see note | `name` 3.0, `topic_tags` 3.0, `aliases` 2.5, `description` 2.0, `body` 1.0 |

**Leave the BM25F values alone unless you're experimenting.** The autopilot
grid-searches them against your own recall hit/miss log and will overwrite
hand-tuned values with measured ones.

### `enforcement` and `enrichment` — the `PreToolUse` hook

| Key | Default | Meaning |
|---|---|---|
| `enforcement.enabled` | `true` | Block `Bash` commands matching a guardrail rule |
| `enforcement.log.maxBytes` | `1048576` | Denial log rotation threshold |
| `enrichment.enabled` | `true` | Surface matching rules as context on `Edit`/`Write` |
| `enrichment.maxRulesPerCall` | `3` | Rules surfaced per tool call |
| `enrichment.bodyPreviewChars` | `300` | Characters of rule body included |
| `enrichment.maxEmissionsPerSession` | `15` | Cap per session |
| `enrichment.log.maxBytes` | `1048576` | Log rotation threshold |

### `scoping` and `doctor`

| Key | Default | Meaning |
|---|---|---|
| `scoping.universalThreshold` | `2` | Projects a rule must appear in before it's promoted to universal |
| `doctor.skipStatuslineDrift` | `false` | Silence the statusLine drift check — useful if you manage `settings.json` by hand |

## Environment overrides

- `MNEMO_CONFIG_PATH` — load config from this path instead of the default

## Turning capture off

```json
{ "capture": { "sessionStartEnd": false } }
```

Then run `mnemo status` to confirm. This only stops the session markers — to
stop mnemo doing anything at all, uninstall it; see
[getting-started.md](getting-started.md).
