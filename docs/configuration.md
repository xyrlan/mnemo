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

Two things the debounce arithmetic does not do. **The first extraction of a
vault is never debounced**: a vault that has never been extracted has no pages
to count, and only an extraction produces them, so the count gate would hold a
fresh install back forever. A missing last-run marker runs immediately.
**Briefings count as new material** toward `extraction.auto.minNewMemories`,
alongside memory files — a session whose only product is a correction mutates
no files but does write a briefing, and that briefing is exactly what
consolidation reads. The time gate is unaffected by both: new material does not
buy a pass through `extraction.auto.minIntervalMinutes`.

`mnemo learn` bypasses the debounce entirely. It runs the same two stages in
the foreground regardless of when the last run was, and scopes extraction to
the briefing for the current session, so it never sweeps another project's
backlog into LLM calls. See
[Five minutes](getting-started.md#five-minutes).

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

The automatic sweep is **off by default**. To opt in:

```json
{ "backfill": { "autoOnFirstSession": true } }
```

When on, it runs **once per vault**, and only after it finishes: a sweep that
dies because the `claude` CLI is unreachable leaves the one-shot unspent, and
the next session tries again. Everything it produces is staged under
`shared/_inbox/` for review, never promoted — and while those staged pages are
the only rules in the vault, every session start carries one line saying how
many are waiting and what to do with them (see the `[mnemo] … staged` notice
under [What mnemo tells the agent at session start](#what-mnemo-tells-the-agent-at-session-start)).

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

The most relevant rule (or two, when a second one also clears the floor and
the overlap check), injected inline before Claude answers.

| Key | Default | Meaning |
|---|---|---|
| `reflex.enabled` | `true` | Run retrieval on every prompt |
| `reflex.maxEmissionsPerSession` | `10` | Stop injecting after this many hits in one session |
| `reflex.thresholds.termOverlapMin` | `2` | Query/rule terms that must overlap |
| `reflex.thresholds.relativeGap` | `1.0` | How far the top hit must beat the runner-up. `1.0` turns the check off (the default since #332: a near-tie means two rules apply, and both are injected); set it above `1.0` to silence prompts without a clear winner |
| `reflex.thresholds.absoluteFloor` | `2.0` | Minimum score to inject at all (scaled down in small vaults, see next row) |
| `reflex.thresholds.floorReferenceDocs` | `30` | Below this many rules the floor is scaled down with the vault's idf ceiling, so a young vault can inject at all |
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
| `enrichment.enabled` | `true` | Surface rules whose `activates_on.path_globs` name the file as context on `Read`/`Edit`/`Write` |
| `enrichment.maxRulesPerCall` | `3` | Rules surfaced per tool call |
| `enrichment.bodyPreviewChars` | `300` | Characters of rule body included |
| `enrichment.maxEmissionsPerSession` | `15` | Cap per session; each rule is surfaced at most once per session |
| `enrichment.log.maxBytes` | `1048576` | Log rotation threshold |

### `recall` — ordering what the agent is offered

`list_rules_by_topic(topic, query=...)` orders a topic with BM25F, locally.
`recall.rerank` is an opt-in second stage: a model that reads each (task, rule)
pair **marks** the rules worth reading and puts them first.

Marking, not filtering, and never dropping: every rule the judge scored comes
back with `relevant: true` or `relevant: false`, the marked ones at the top of
the list, and every other rule still in it. A rule the judge did not score —
one past `maxRules`, one with an empty body, one the judge skipped — carries no
`relevant` key at all: absent means "not judged", which is not the same answer
as `false`. The tool description tells the agent to read the true ones first,
and an empty set of true ones is a real answer: nothing in this topic is about
this task.

```json
{ "recall": { "rerank": { "provider": "typesafe" } } }
```

| key | default | |
|---|---|---|
| `recall.rerank.provider` | `"none"` | `"typesafe"` turns the stage on. Anything else is read as `"none"`. |
| `recall.rerank.model` | `"jev-1.13.0"` | Pinned. Never an alias such as `jev-latest`: the measurement below is of this model. |
| `recall.rerank.keyEnv` | `"TYPESAFE_API_KEY"` | The environment variable holding the key. The key is never read from the config file. |
| `recall.rerank.timeoutSeconds` | `4` | One request per list call; past this the BM25F order is returned. |
| `recall.rerank.maxRules` | `64` | A larger topic sends its first 64 rules in BM25F order and leaves the rest where they were, unmarked. |
| `recall.rerank.bm25Weight` | `0.5` | How much of the local BM25F score, divided by the best one in that list, is added to the judge's probability. `0` is the judge alone. |
| `recall.rerank.relevantAt` | `0.69` | The bar that sum has to clear to be marked `relevant: true`. |

**What leaves the machine when it is on:** for each `list_rules_by_topic`
call that carries a `query`, that query and the first 800 characters of every
rule in the topic (link section removed) are posted to
`api.typesafe.ai`. No slug, path, project name or transcript is sent. Nothing
is sent by the per-prompt reflex, by `mnemo recall`, or by any hook — the MCP
server is the stage's only caller. A missing key, a timeout, an HTTP error or
a malformed answer all return the BM25F order, silently to the agent; the
access log records which (`rerank.status` in `.mnemo/mcp-access-log.jsonl`).

**Turning it on.** `mnemo rerank --setup` prints the paragraph above, asks for
a `y`, reads the key without echoing it — never as a command-line argument,
which would land in shell history and in `ps` — makes one test request with a
fixed harmless task and rule of its own, and writes nothing at all if that
request fails.

**Where the key lives, and why not here.** Not in this file and not in the
vault: `mnemo.config.json` sits *inside* the vault, which may be a git repo or
a synced folder, and a repo-local config is committed. It goes in
`~/.mnemo/secrets.json` instead, shaped `{"recall.rerank": {"typesafe": "…"}}`
so a second provider is a second entry rather than a second file, and created
owner-only — `0600`, inside a `0700` directory — on the platforms that have
file modes. Windows has none, and there the file gets whatever that filesystem
gives it. `MNEMO_SECRETS_PATH` overrides the location.

The environment variable still wins: `recall.rerank.keyEnv` is read first, so
a key exported for one shell beats the stored one and a second key can be
tried without editing anything. The file exists because the process that reads
it is the MCP server, which **Claude Code spawns, not your shell** — an
`export` in `.zshrc` reaches it only when `claude` itself was started from that
shell, and never when the app was opened from the Dock.

**Turning it off.** `mnemo rerank --off` sets the provider back to `"none"`
and takes the stored key off the machine. Both halves are idempotent.

**Seeing whether it is actually running.** Every failure is silent to the
agent by design, so three commands say otherwise, none of them making a
network call and none of them able to print the key:

- `mnemo rerank` — the provider, the model, whether the key comes from the
  environment, the secrets file or nowhere, and the last 14 days of `rerank`
  rows from the access log by status, with how many rules were judged and
  marked. `--json` for the same as data, `--days N` for a different window.
- `mnemo doctor` — a `rerank` row that fails when the provider is set and no
  key resolves, which is exactly the state where the list looks unchanged
  because every call fell back.
- `mnemo status` — the same summary in one line, printed only when the
  provider is set.

**Why it marks instead of reordering, and how sure that is.** The list an
agent is handed is 15 rules of which three quarters are about something else,
and it picks from slugs alone. Reordering cannot fix that: ordering those lists
by the labels themselves — the best order there is — still leaves 19 of 85
top-5 slots on irrelevant rules, because most topics do not hold five rules
worth reading. The report prints that ceiling as its `oracle` row. `tools/measure_rerank_filter.py` measured both uses of
the same signal over 85 real queried `list_rules_by_topic` calls from this
machine's session transcripts (1,536 (query, rule) pairs, every pair labelled
0/1/2), with the thresholds fixed on a dev split of 55 queries and the
remaining 30 opened once:

| what the list offers | rules / query | junk | should-read kept | queries left empty (that had a should-read) |
|---|---|---|---|---|
| whole list (today) | 15.0 | 75% | 24/24 | 0 |
| BM25F score ≥ 1.94 | 5.1 | 47% | 23/24 | 10 (0) |
| judge's `noul` ≥ 0.42 | 2.8 | 26% | 24/24 | 14 (0) |
| fused: `noul` + 0.5 × BM25F/max ≥ 0.69 (shipped) | 2.3 | 13% | 24/24 | 11 (0) |

As a *reranker* the same signal moved nDCG@5 by +0.081, 95% paired bootstrap
interval [−0.024, +0.185] — not established. As a *filter* it turns 15 rules
into 2.3 and 75% junk into 13% while keeping every rule the labels call "should
read". It costs about 1 s per list call and about $0.00004.

Four limits on that. The labels are a model's (`claude-fable-5-1`, blind, one
0/1/2 per pair), never the developer whose task it was — and they are not
stable: of the 107 pairs this rater was asked about twice, in a different
context, 84 came back with the same label, which the same report prints. There
are only 24 "should read" rules in the test split, so "24/24" is a small number
kept, not a rate with a tight interval. On the dev split, where the thresholds
were fitted, the shipped bar keeps 43 of 47 and leaves one query holding a
should-read rule empty — that is the honest spread. And whether an agent
offered two marked rules actually reads them is not measured at all. Treat it
as an experiment you can re-run on your own vault: the report above is
`PYTHONPATH=src python3 tools/measure_rerank_filter.py`, and only its
`--score --send` flag reaches the provider.

### `resume` — waking a child the account's limit stopped

A dispatched child that hits the five-hour window stops mid-turn and its
process dies. `mnemo resume` wakes it by hand; with `resume.auto` on, mnemo
wakes it itself once the reset its own transcript recorded has passed, and
nobody has to be at the keyboard for it.

The trigger is a small watcher process (`mnemo resume --watch`) that
`SessionStart` starts while there is something to watch and that exits when
there is not. It is not a hook, because the limit stops every Claude session
on the machine — measured on 2026-09-19, no mnemo hook fired at all between
the stall and four hours past the reset — so the thing that waits out the
clock has to be something that needs no API of its own. `tools/measure_wake_latency.py`
is that measurement, re-runnable.

Only a rate limit that named its own reset epoch is ever woken automatically,
and each child at most once per reset window: a child that stalls again waits
for the next boundary rather than being woken in a loop. A stall that needs a
person — a login, a billing decision, a question — is never touched. Every
wake lands in `.mnemo/resume-wakes.json`, in the day log of the repo the child
was working in, and in a `mnemo sessions` footer.

| Key | Default | Meaning |
|---|---|---|
| `resume.auto` | `true` | Wake a rate-limited child once its reset has passed, with no command typed. `false` leaves it to `mnemo resume`. |
| `resume.maxPerPass` | `5` | Children one pass wakes; the rest wait for the next check, a minute later. |

### `scoping`, `install` and `doctor`

| Key | Default | Meaning |
|---|---|---|
| `scoping.universalThreshold` | `2` | Projects a rule must appear in before it's promoted to universal |
| `doctor.skipStatuslineDrift` | `false` | Silence the statusLine drift check — useful if you manage `settings.json` by hand |
| `install.autoRepairHooks` | `true` | Rewrite mnemo's hook entries at session start when an installed matcher is narrower than the one this version ships (#337). Once per distinct drift, `settings.json` hooks only, previous file backed up. `false` keeps a matcher you narrowed by hand — `mnemo status` and `mnemo doctor` still report it. |

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
behalf, and each part can be switched off. At most five pieces appear, and the
last of them is one of two review offers — never both (see below):

1. **The topic envelope** — the list of memory topics available for this
   project, so the agent knows what it can ask for. Controlled by
   `injection.enabled` and `injection.maxTopicsPerScope`.

2. **The first-run notice** — a single line beginning `[mnemo] first run …`,
   shown once per repo when past transcripts are available to backfill and
   `backfill.autoOnFirstSession` is off. See the `backfill` section above.

3. **The staged-reconstruction notice** — a single line beginning
   `[mnemo] N rule(s) reconstructed from your past sessions are staged …`,
   shown while backfilled rules are waiting in `shared/_inbox/` **and the
   vault has no live rule at all**. It is read from the vault on every
   session start, with no once-shown marker: it repeats while that is true
   and stops on its own once you move or delete the staged pages, or any
   rule goes live. It never promotes anything.

4. **The learned-rule announcement** — what extraction promoted since this
   project last looked, opened by `[mnemo learned since your last session]`
   and closed by `[/mnemo learned]`. One bullet per rule, each ending in its
   undo: `veto: mnemo disable-rule <slug>`. A rule marked `verified` also
   shows the sentence it was learned from; an `inferred` one shows none,
   because there is no real quote behind it. At most 5 bullets ride on the
   prompt — the rest are counted on a trailing line and listed in full by
   `mnemo status` under **Recently learned**.

5. **The staged-page offer** — what extraction *staged* rather than promoted,
   opened by `[mnemo staged for review — project=<name>, N waiting]` and closed
   by `[/mnemo staged]`. One bullet per page: its key, its description, how old
   it is, why it staged, and the command that acts on it — `mnemo inbox
   --promote <key>`. A staged page is invisible to recall, so whatever it holds
   carries nothing while it waits; this is what puts it in front of you while
   you are already working on the project it came from. It only reads: nothing
   promotes, accepts or drops a page without you.

   Three numbers under `inbox` bound it, because it rides on the same prompt as
   the briefing: `offerMax` (2) bullets per block, at most one block per project
   per `offerIntervalHours` (24), and no page offered twice inside
   `offerCooldownDays` (7) — so the queue rotates instead of repeating its head.
   `offerOnSessionStart: false` silences the block; `mnemo inbox` still lists
   the queue. Every offer and every decision is appended to
   `.mnemo/inbox-offers.jsonl`, which is what makes `mnemo inbox --stats` able
   to say how many pages were resolved this week and how long they took.

6. **The procedure offer** — one `CLAUDE.md` line two or more dispatched
   children of this repo worked out for themselves and the file does not say,
   opened by `[mnemo procedure candidate — repo=<name>, N undecided]` and closed
   by `[/mnemo procedures]`. The bullet names the key, the command as a child
   would have to type it, how many of the repo's children ran it the hard way
   first, and the one act that ends it: `mnemo procedures --accept <key>`.
   Nothing is written to any repo without you.

   Bounded by the same three numbers under `procedures`, with `offerMax` **1**
   rather than 2: accepting writes a permanent line into a file every session
   in that repo then reads, so one decision at a time is the right price.
   `offerOnSessionStart: false` silences it and leaves `mnemo procedures`
   working. Offers and decisions are appended to
   `.mnemo/procedure-decisions.jsonl`, which is what `mnemo procedures --stats`
   reads to say how long a candidate takes to go from shown to decided.

   Two things are particular to this block. It never fires inside a dispatch
   worktree — a dispatched child is the party that *paid* for the missing line,
   not the one who writes it, and without that guard a dispatch of eight
   children would spend the day's one offer on a session no maintainer reads.
   And it reads a cache rather than scanning: finding candidates means reading
   every dispatch transcript on disk (~1.0 s over the 184 there on 2026-09-19),
   which the session-start path must not pay, so session start spawns
   `mnemo procedures --refresh` detached at most once per
   `refreshIntervalHours` (24) and the block reads what the last refresh wrote.
   Run `mnemo procedures --refresh` yourself if you want the offer to see
   something now. The two staleness cases that would matter are read live, not
   from the cache: a candidate you already accepted or dropped, and a line you
   wrote into `CLAUDE.md` by hand, are both silent immediately.

**One offer per session start.** Blocks 5 and 6 are both review queues asking
for a decision, and two of them in one prompt is a nag — so they share a single
slot, and it goes to whichever has gone longest without it. Each keeps its own
`offerIntervalHours`, so they alternate rather than one starving the other; a
queue that wins the slot with nothing to say hands it straight back, so the
alternation never costs you an offer. The worst case on the prompt is therefore
one block: ~190 tokens for the staged one, ~100–140 for the procedure one,
against a briefing that costs ~1783 tokens at 90.9% of session starts.

A rule mnemo wrote silently is a rule you cannot correct, which is why the
announcement exists at all: extraction writes into the vault on its own, so
the veto has to be one line away rather than three commands deep in a
directory you have never opened.

Two files under the vault's `.mnemo/` back the fourth block:

- `.mnemo/learned.jsonl` — the append-only ledger, one line per promoted rule.
- `.mnemo/announced.json` — the per-project high-water mark, so a rule is
  announced exactly once per project. Deleting it re-announces the backlog;
  deleting the ledger loses the history without breaking anything.

## Maintenance commands

### `mnemo inbox`

The review queue for `shared/_inbox/`. Extraction stages a page there whenever
it will not promote it on its own — a feedback page whose quote failed the
evidence gate, a reconstruction from an old transcript, a page whose sources
span two projects — and a staged page is served to nobody until you move it.

```bash
mnemo inbox                       # staged for the project you are standing in
mnemo inbox --all                 # every project
mnemo inbox --show KEY            # print one page before deciding
mnemo inbox --promote KEY         # into shared/<type>/, where recall reaches it
mnemo inbox --drop KEY            # archived under shared/_archive/dropped-<run>/
mnemo inbox --stats               # depth, median age, what drained this week
```

`KEY` is `<type>/<slug>`, or a bare slug while only one type holds it. Both
decisions also write `.mnemo/extraction-state.json`, which a hand `mv` did not:
a promoted page's entry becomes `promoted`, so the next extraction does not
stage an update proposal for a source that never changed, and a dropped page's
becomes `dismissed`, so it does not come back unless you run `mnemo extract
--force`. `mnemo rewrites` is the sibling command for the other half of that
directory — the `.proposed.md` rewrites of rules that are already live.

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
- `MNEMO_HOOKS_OFF` — when set to anything but `0`/`false`/`no`/`off`, every
  mnemo hook returns immediately: no logging, no injection, no enforcement, no
  background work. mnemo sets it on the `claude --print` helpers it launches
  for itself, so a briefing or an extraction does not fire the hooks that
  would schedule another one (#329). Setting it in your own shell silences
  mnemo for every session started from it; `mnemo doctor` says so when it sees
  it set.

## Turning capture off

```json
{ "capture": { "sessionStartEnd": false } }
```

Then run `mnemo status` to confirm. This only stops the session markers — to
stop mnemo doing anything at all, uninstall it; see
[getting-started.md](getting-started.md).
