# Friction ledger — the vault learns what contradicted it

**Status:** design, approved 2026-09-16
**Subsystem 1 of 3.** Blocks: refutation (subsystem 2), friction-driven extraction (subsystem 3).

## The problem

mnemo's learning loop is open. A session produces rules; nothing ever tells a rule
it was wrong.

Measured on this vault, 2026-09-16:

| | |
|---|---|
| live rules | 1946 |
| rules with more than one source | **41 (2.1%)** |
| median sources per rule | 1 |
| `carried_correction_backed` (replay, any gate setting) | **0** |

97.9% of rules were written once and never touched again — not reinforced, not
contradicted, not retired. The vault only grows on its own; it shrinks only when
the maintainer runs `reclassify`, `reverify` or `stale` by hand. Rule count by
month: 9 (May), 18, 48, 81, 191 (Sept).

The 2026-09-01 product audit recorded the same 98% single-source figure at 1648
rules. A year later, at 1946, the number has not moved, because nothing in the
system acts on it.

**Contradiction today is resolved in prose, not structure.** When a rule is
corrected, the correction is written into the rule's body — the vault's
`merge-requires-admin` page carries `**CORRECTED 2026-09-12**` inside its text.
A human reader sees it; the ranker does not. The old claim keeps its rank and
competes with the new one forever.

## The finding that makes this tractable

The signal already exists and is thrown away.

```
briefings on disk                      398
  with a `## Corrections` section       42
  of those, non-empty                   42   (100%)
correction items                        70   → 1.67 per briefing
```

Where the extractor asks for corrections, it finds them **every time**. The 356
briefings without the section predate the prompt that introduced it; they are not
frictionless sessions, they are unasked ones.

Rate over the measured week (09-08 → 09-15): **70 corrections in 8 days, ~8.75/day**,
peaking at 26 in one day. By project: mnemo 36, clubinho 15, clearframe 6,
sg-imports 4.

These are real friction, not narration:

```
"essa regra de sempre fazer backup antes de um deploy cegamente,
 nao pode se tornar uma ma pratica?"
"pode fazer a correcao que voce acha melhor, nos temos autoridade para isso"
"nos vamos comentar todo esse grupo de camadas por enquanto"
```

So `carried_correction_backed = 0` is not a shortage of raw material. It is the
absence of a mechanism. Each correction becomes a line of briefing prose and dies
there: it marks no rule, feeds no ranking, retires nothing.

## What this subsystem builds

A **friction ledger**: every correction becomes a structured record that names the
rule it contradicts, instead of prose inside a briefing.

This spec covers the ledger and its backfill only. Acting on the ledger at query
time is subsystem 2; changing what gets extracted is subsystem 3.

### Design decisions

**Mechanism: friction, not post-hoc judgement.** The alternative considered was an
LLM reading each transcript afterwards and scoring whether the session followed an
injected rule. Rejected: it measures what a session did, not what it would have
done without the rule — the same confound as the `hindsight` bucket in `replay` —
and it is an instrument, not a product mechanism. Friction is the signal this
vault has already proven (#272 built the CI half of it).

**Action on contradiction: death with a trail.** A contradicted rule leaves the
candidate pool immediately and records who superseded it. Alternatives rejected:

- *weight penalty* — leaves a wrong rule circulating
- *quarantine queue* — creates review work with no owner; `shared/_inbox/` stands
  at 194 pages, having been drained to 1 once already
- *silent deletion* — unrecoverable

The trail replaces the queue: the system acts, and the maintainer audits later.
This is the pattern `reclassify` already uses successfully (`_archive` + `--undo`).

**Linking a correction to a rule: extractor judgement, confirmed by injection.**
Contradiction is semantic, not lexical. `merge-requires-admin` ("needs `--admin`"
→ "run it directly") is a pure contradiction in near-identical vocabulary; lexical
similarity cannot see it, and this vault has already recorded that Jaccard scores
a true cross-type duplicate at 0.136, below the p90 of unrelated noise (0.131).

The extractor already reads the briefing and already receives live rules as the
`existing_rules` hint. It gains one question: *does this correction contradict any
of these?* No new LLM call.

When the reflex also injected that rule into the session the correction came from,
the link is corroborated and recorded as such.

## Data model

One record per correction, appended to `<vault>/.mnemo/friction-ledger.jsonl`:

```json
{
  "id": "f-20260916-3a7c1e",
  "ts": "2026-09-16T02:50:04Z",
  "session_id": "e7fb983c-...",
  "project": "mnemo",
  "quote": "<verbatim user words>",
  "rule_text": "<the imperative the briefing derived>",
  "briefing": "bots/mnemo/briefings/sessions/<id>.md",
  "contradicts": ["mnemo__merge-requires-admin"],
  "link_basis": "extractor" | "extractor+injected" | "none",
  "injected_in_session": ["mnemo__merge-requires-admin"],
  "origin": "user" | "ci",
  "backfilled": false
}
```

Reuses `core.corrections.Correction(quote, rule)` and its mechanical check that
the quote is a real substring of something the user typed — a fabricated quote
never reaches the ledger. `origin` reuses `ci_corrections.ORIGIN_USER` /
`ORIGIN_CI` so the CI channel (#272) writes to the same ledger.

Same telemetry switch, 1 MiB rotation and never-raises discipline as
`briefing-log.jsonl` (channels). Its own file, so a busy correction day does not
shorten the window `mnemo recall` reads.

### Frontmatter

Two new optional keys, written by subsystem 2 but defined here so the ledger and
the pages agree:

- `superseded_by: <slug>` on the retired rule
- `supersedes: [<slug>, ...]` on the rule that replaced it

A page carrying `superseded_by` stays on disk and stays readable. Only its
eligibility as a reflex candidate changes, and only in subsystem 2.

## Components

**`core/friction/ledger.py`** — append, read, rotate. One public `record()` and one
`iter_records()`. Knows nothing about rules or extraction.

**`core/friction/link.py`** — given a `Correction` and the candidate rules the
extractor saw, produce `contradicts` and `link_basis`. Pure; no I/O; the unit that
the backfill and the live path share.

**`core/extract/prompts/templates/`** — the consolidation prompt gains the
contradiction question and a schema field for the answer.

**`core/friction/backfill.py`** — the retroactive pass (below).

**`cli/commands/friction.py`** — `mnemo friction` reports the ledger: counts by
project and origin, what contradicts what, and what the backfill recovered.
Read-only.

### Known constraint: the extractor's field of view

`existing_rules` quotes at most `MAX_BODIES = 3` rule bodies per chunk, gated at
`BODY_RELEVANCE_THRESHOLD = 0.12`; everything else is a one-line `slug — name`.
So the contradiction question is asked against 3 full bodies and up to
`MAX_ENTRIES = 80` titles.

This is a deliberate ceiling, not an oversight: raising it costs ~17k tokens per
chunk. The consequence is that contradictions against a rule outside the relevant
3 will be missed, and the ledger will under-report rather than over-report. The
backfill's measurement below is what says whether that ceiling needs raising, and
that decision is deferred until there is a number.

## Backfill

The ledger is worth little until it has history, and the history is recoverable:

- **356 briefings** have no `## Corrections` section because they predate the
  prompt, not because their sessions were frictionless
- **33 of 37** sessions that received a reflex injection still have transcripts on
  disk

`mnemo friction --backfill` re-reads those briefings' source transcripts through
the ordinary correction path — the same prompt in use today, into a scratch root,
one Haiku call per session — and appends what it finds with `backfilled: true`.
This is the same shape as `mnemo reverify` (#257), which re-briefs sessions into a
scratch root and reuses the result on a rerun.

Dry run by default, printing per session: *corrections found*, *none found*,
*transcript gone*. `--apply` writes exactly what the dry run showed, with no
second LLM pass.

At the measured rate of 1.67 corrections per briefing, 356 briefings suggest
roughly 500–600 recoverable records — but the rate was measured on September
sessions under the current prompt, and older sessions may differ, so the dry run
is the number that counts, not this estimate.

## Error handling

Every failure degrades to "no ledger record", never to a broken extraction or a
lost briefing:

- ledger write fails → log one `.errors.log` row, extraction continues
- extractor returns no contradiction field (old prompt, malformed response) →
  record with `contradicts: []` and `link_basis: "none"`
- quote fails `corrections.verify` → not recorded at all, as today
- transcript missing during backfill → reported, never fabricated
- `contradicts` names a slug not in the vault → recorded, flagged by
  `mnemo friction`, never written to frontmatter

The circuit breaker (#314) governs the hook path unchanged.

## Testing

- **Unit**: `link.py` against fixtures drawn from the 70 real corrections,
  including the `merge-requires-admin` case where lexical similarity fails and the
  semantic judgement must carry it.
- **Unit**: ledger append, rotation at 1 MiB, never-raises on a read-only vault.
- **Contract**: a correction whose quote does not verify never reaches the ledger.
- **Integration**: one extraction run end to end writes ledger rows whose
  `session_id` and `briefing` resolve to real files.
- **Real-data**: the backfill dry run over the actual 356 briefings, asserting it
  reports rather than raises on every missing transcript.
- No test may assert a fixed count from the maintainer's vault — those are
  measurements, and they belong in the report, not in an assertion.

## What this subsystem does not do

- It does not change what the reflex injects. A `superseded_by` page still
  competes until subsystem 2 ships.
- It does not change what gets extracted. The 191-rules-a-month rate is
  subsystem 3.
- It does not delete or rewrite any page.
- It does not judge whether an injected rule helped. That was the rejected
  post-hoc approach, and it stays rejected.

## Success criteria

1. Every correction the extractor finds appears in the ledger with a verified quote.
2. The backfill dry run reports a per-session outcome for all 356 briefings without
   raising.
3. `mnemo friction` states how many live rules stand contradicted — a number the
   vault has never been able to produce.
4. Full suite green; no change to what the reflex injects.

Once the ledger has history, subsystem 2 can act on it, and the open question the
vault has never been able to ask — *how much of what I hold has already been
contradicted?* — has an answer.

## Related

#332 (relative_gap), #333 (calibrator target), #335 (negative results: scope and
floor are not the bottleneck), #272 (CI corrections), #257 (reverify), #244
(evidence gate both directions).
