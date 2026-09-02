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
| `backfill.autoOnFirstSession` | `false` | No automatic one-time sweep of your old transcripts on the first session. `mnemo backfill` still works by hand |

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
| `extraction.subprocessTimeout` | `60` | Seconds before an extraction call is abandoned |
| `extraction.costSoftCap` | `null` | Warn past this spend, in dollars. `null` = no cap |
| `extraction.auto.enabled` | `true` | Run extraction automatically at `SessionEnd` |
| `extraction.auto.minNewMemories` | `1` | New source files required before a run |
| `extraction.auto.minIntervalMinutes` | `60` | Minimum gap between automatic runs |

Every LLM call mnemo makes — extraction, briefings, backfill — shells out to
the `claude` CLI you already have and uses whatever authentication it already
has. There is no separate credential to configure, and no switch here that
changes it: on a Pro/Max subscription those calls carry no per-token charge, on
API-key auth they are billed.

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
| `backfill.autoOnFirstSession` | `false` | Run the one-time backfill automatically on the first session (off: the first session shows a one-line invitation instead) |

The automatic sweep runs **once per vault**, and only after it finishes: a
sweep that dies because the `claude` CLI is unreachable leaves the one-shot
unspent, and the next session tries again.

To upgrade without it ever running:

```json
{ "backfill": { "autoOnFirstSession": false } }
```

**The first-run notice.** With `backfill.autoOnFirstSession` left at its
default `false`, nothing is swept automatically. Instead, the first session in
a repo that has harvestable transcripts prints one line telling you how many
past sessions are available, what `mnemo backfill` would cost in Haiku calls
(capped by `backfill.installCap`), and that `mnemo backfill --dry-run` shows
the exact figure. The invitation is shown **once per repo**, not once per
vault: the backfill ledger records `firstRunNoticeShown` per project, so
adding mnemo to a second repo invites you there too, and neither repo nags
after the first time. A repo with no harvestable transcripts is never shown
the notice at all.

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
| `reflex.bm25f.fieldWeights.*` | see note | `name` 3.0, `topic_tags` 3.0, `aliases` 2.5, `evidence` 2.5, `description` 2.0, `body` 1.0 |

**Leave the BM25F values alone unless you're experimenting.** The autopilot
grid-searches them against your own recall hit/miss log and will overwrite
hand-tuned values with measured ones.

### Rule frontmatter written by extraction

Every extracted page carries these keys; they are written by mnemo, not
configured.

| Frontmatter key | Values | Meaning |
|---|---|---|
| **confidence** | `verified` / `inferred` | `verified` means the rule cites a quote the user actually typed (see `evidence`). Everything else is `inferred`. |
| **evidence** | `{quote, source}` | The verbatim user quote a feedback rule was built from and the briefing it comes from. Only `verified` feedback pages carry one. The reflex scores this quote as its own field. |
| **demoted_from** | `feedback` | The page was extracted as feedback but had no verifiable quote, so it was staged as a `reference` page in `shared/_inbox/reference/` for review. |

A feedback rule reaches `shared/feedback/` only when its quote verifies
against the `## Corrections` section of one of its own source briefings — a
section the briefing writer itself checks against the transcript. Rules that
mnemo cannot trace back to your words never auto-promote; they wait in the
inbox.

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

### `autopilot` — what may leave the machine

| Key | Default | Meaning |
|---|---|---|
| `autopilot.network.enabled` | `false` | Allow the autopilot to call `gh` (digest issues, self-fix PRs, outcome polling). Everything local runs regardless. |

With the switch off, the autopilot still does its local work: self-fix cures
are applied in place in your vault and every run is logged to
`.mnemo/autopilot-runs.log`, so you can read exactly what it changed. What
stops is anything that talks to GitHub — no digest issues are filed, no
self-fix PRs are opened, no outcomes are polled. Set
`autopilot.network.enabled` to `true` to get those back:

```json
{ "autopilot": { "network": { "enabled": true } } }
```

## What mnemo tells the agent at session start

At `SessionStart` mnemo hands the agent a block of context. Everything in it
is disclosure — you can read exactly what mnemo is telling the agent on your
behalf, and each part can be switched off. At most three pieces appear:

1. **The topic envelope** — the list of memory topics available for this
   project, so the agent knows what it can ask for. Controlled by
   `injection.enabled` and `injection.maxTopicsPerScope`.

2. **The first-run notice** — a single line beginning `[mnemo] first run …`,
   shown once per repo when past transcripts are available to backfill and
   `backfill.autoOnFirstSession` is off. See the `backfill` section above.

3. **The learned-rule announcement** — what extraction promoted since this
   project last looked, opened by `[mnemo learned since your last session]`
   and closed by `[/mnemo learned]`. One bullet per rule, each ending in its
   undo: `veto: mnemo disable-rule <slug>`. A rule marked `verified` also
   shows the sentence it was learned from; an `inferred` one shows none,
   because there is no real quote behind it. At most 5 bullets ride on the
   prompt — the rest are counted on a trailing line and listed in full by
   `mnemo status` under **Recently learned**.

A rule mnemo wrote silently is a rule you cannot correct, which is why the
announcement exists at all: extraction writes into the vault on its own, so
the veto has to be one line away rather than three commands deep in a
directory you have never opened.

Two files under the vault's `.mnemo/` back the third block:

- `.mnemo/learned.jsonl` — the append-only ledger, one line per promoted rule.
- `.mnemo/announced.json` — the per-project high-water mark, so a rule is
  announced exactly once per project. Deleting it re-announces the backlog;
  deleting the ledger loses the history without breaking anything.

## Maintenance commands

### `mnemo reclassify`

Grades every live rule in `shared/feedback/` under the evidence rules above,
one Haiku call per ten rules. Verdicts: `keep` (a user quote supports it —
the page gains `confidence: verified` and an `evidence` block), `demote`
(real knowledge, no correction — moved to `shared/reference/`), `merge`
(same rule as another slug — sources folded into it), `archive` (generic
advice, narrative, or the extractor's own instructions echoed back).

```bash
mnemo reclassify --limit 30     # trial: grade 30 rules, save the plan, change nothing
mnemo reclassify                # full plan (~1 call per 10 rules), saved to .mnemo/reclassify-plan.json
mnemo reclassify --apply        # execute the saved plan — no LLM calls
mnemo reclassify --undo <RUN_ID>  # restore every touched file byte-for-byte
```

Every file the run touches is copied first into
`shared/_archive/reclassify-<RUN_ID>/originals/`, and a `manifest.json`
records each move, so `--undo` is exact. The archive directory is left in
place as the audit trail.

## Environment overrides

- `MNEMO_CONFIG_PATH` — load config from this path instead of the default

## Turning capture off

```json
{ "capture": { "sessionStartEnd": false } }
```

Then run `mnemo status` to confirm. This only stops the session markers — to
stop mnemo doing anything at all, uninstall it; see
[getting-started.md](getting-started.md).
