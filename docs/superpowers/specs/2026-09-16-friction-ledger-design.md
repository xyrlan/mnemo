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

**Linking a correction to a rule: a dedicated contradiction pass, corroborated by
injection.** Contradiction is semantic, not lexical. `merge-requires-admin`
("needs `--admin`" → "run it directly") is a pure contradiction in near-identical
vocabulary; lexical similarity cannot see it, and this vault has already recorded
that Jaccard scores a true cross-type duplicate at 0.136, below the p90 of
unrelated noise (0.131). So the judgement is an LLM's.

The first design asked the question inside the existing consolidation prompt,
against the `existing_rules` hint, on the grounds that it added no LLM call.
Measurement killed that: see *The candidate pool is the real constraint* below.
The hint shows at most `MAX_ENTRIES = 80` rules **ordered by `source_count`**, which
covers 23.5–37.9% of the eligible `reference` pool — and since 97.9% of rules have
`source_count = 1`, which 80 appear is settled by slug order inside a massive tie.
Asking about contradiction against an arbitrary quarter of the vault would produce
a ledger whose silence means nothing.

So contradiction gets its own pass, and it only runs when there is a correction to
resolve — 42 of 398 briefings, ~10%. In that pass the candidates are ranked against
the correction's own text with the BM25F the reflex already uses, not by
popularity, and the top `CONTRADICTION_CANDIDATES` (default 40, bodies included)
are put to the model.

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

**`core/friction/candidates.py`** — given a `Correction` and a vault, rank the whole
eligible pool against the correction text with `core.reflex.bm25` and return the
top `CONTRADICTION_CANDIDATES` with bodies. Reuses the reflex index; adds no new
ranking code.

**`core/friction/link.py`** — given a `Correction` and those candidates, run the
contradiction pass and produce `contradicts` and `link_basis`. One LLM call, and
only when a correction exists. The unit the backfill and the live path share.

**`core/extract/prompts/templates/contradiction.py`** — the pass's own prompt and
response schema. The consolidation prompt is untouched, so #184's edit contract and
the existing `existing_rules` behaviour carry no risk from this change.

**`core/friction/backfill.py`** — the retroactive pass (below).

**`cli/commands/friction.py`** — `mnemo friction` reports the ledger: counts by
project and origin, what contradicts what, and what the backfill recovered.
Read-only.

### The candidate pool is the real constraint

Measured on this vault, 2026-09-16 — what `existing_rules` can actually show,
per kind and agent:

| kind | agent | eligible | shown (80) | coverage |
|---|---|---|---|---|
| feedback | mnemo | 9 | 9 | 100% |
| project | mnemo | 122 | 80 | 65.6% |
| **reference** | **mnemo** | **211** | **80** | **37.9%** |
| **reference** | **clubinho** | **340** | **80** | **23.5%** |

Two compounding problems:

1. **`MAX_ENTRIES = 80` truncates the pool** exactly where the vault is biggest.
   `reference` holds 1682 of 1946 live rules.
2. **The ordering is `source_count` descending, which is noise here.** 97.9% of
   rules have exactly one source, so the top-80 cut lands inside a tie of 122
   (mnemo) or 84 (clubinho) rules and is resolved by slug order — alphabetical, in
   effect.

`existing_rules_fragment` already records this failure for its own purpose: all
four of #184's re-summarized rules "fell outside the top 80", which is why body
relevance scores every eligible rule rather than the listed slice. The title list
that a contradiction question would be asked against was never fixed the same way.

`MAX_BODIES = 3` is *not* the binding constraint. It is a deliberate cost ceiling
(3 × ~216 median tokens vs ~17k for all 80) and it is already relevance-ranked
over the whole pool.

**Resolution.** Raising `MAX_ENTRIES` globally would pay the cost on all
extractions to fix an ordering flaw. Instead the contradiction pass (above) is
separate and rare, and inside it the candidates are BM25F-ranked against the
correction text over the **whole** eligible pool, with `CONTRADICTION_CANDIDATES`
(default 40) bodies quoted. Nothing about the consolidation prompt changes.

The ledger may still under-report — a contradiction against a rule ranked 41st
will be missed — and it never over-reports, because a link is only recorded when
the model names a slug that exists. `mnemo friction` reports the rank distribution
of confirmed links so the 40 can be revisited against evidence.

## Backfill

The ledger is worth little until it has history, and the history is recoverable:

- **356 briefings** have no `## Corrections` section because they predate the
  prompt, not because their sessions were frictionless
- **33 of 37** sessions that received a reflex injection still have transcripts on
  disk

`mnemo friction --backfill` re-reads each session's transcript through the ordinary
correction path — the same prompt in use today, into a scratch root, one Haiku call
per session — then runs the contradiction pass on whatever it finds, and appends
the records with `backfilled: true`. Same shape as `mnemo reverify` (#257): scratch
briefings are reused on a rerun, `--fresh` re-briefs.

**Scope: every session with a transcript on disk, not only the 356.** `replay`
already reads 395 sessions; a session that never produced a briefing can still have
contradicted a rule. Restricting the sweep to briefed sessions would inherit
exactly the sampling bias this subsystem exists to remove. Sessions are swept
newest-first, so an interrupted run has recovered the most relevant history.

Dry run by default, printing per session: *corrections found*, *none found*,
*transcript gone*. `--apply` writes exactly what the dry run showed, with no second
briefing pass. `--since DATE` and `--project` bound a run.

At the measured rate of 1.67 corrections per briefing, the 356 unasked briefings
alone suggest roughly 500–600 recoverable records; the wider sweep may find more.
That rate was measured on September sessions under the current prompt and older
sessions may differ, so the dry run is the number that counts, not this estimate.

## Error handling

Every failure degrades to "no ledger record", never to a broken extraction or a
lost briefing:

- ledger write fails → log one `.errors.log` row, extraction continues
- the contradiction pass fails, times out, or returns malformed JSON → the
  correction is still recorded, with `contradicts: []` and `link_basis: "none"`.
  A correction is never lost because the link could not be resolved.
- the contradiction pass is unavailable (no `claude` on PATH, network off) →
  same as above; the ledger degrades to an unlinked record
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
- **Real-data**: the backfill dry run over the real sessions on disk, asserting it
  reports rather than raises on every missing transcript.
- **Real-data**: `candidates.py` ranks the whole eligible pool — a fixture where
  the true contradiction sits outside a `source_count`-ordered top-80 must still
  surface it, which is the failure this design exists to avoid.
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
2. The backfill dry run reports a per-session outcome for every session with a
   transcript on disk, without raising.
3. `mnemo friction` states how many live rules stand contradicted — a number the
   vault has never been able to produce — and the rank distribution of confirmed
   links, so `CONTRADICTION_CANDIDATES` can be revisited against evidence.
4. Full suite green; no change to what the reflex injects and no change to the
   consolidation prompt's output.

Once the ledger has history, subsystem 2 can act on it, and the open question the
vault has never been able to ask — *how much of what I hold has already been
contradicted?* — has an answer.

## Related

#332 (relative_gap), #333 (calibrator target), #335 (negative results: scope and
floor are not the bottleneck), #272 (CI corrections), #257 (reverify), #244
(evidence gate both directions).
