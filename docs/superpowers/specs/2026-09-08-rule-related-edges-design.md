# Rule-to-rule edges for retrieval expansion

**Date:** 2026-09-08
**Status:** REJECTED at the measurement gate, 2026-09-08. The design rests
on a misdiagnosis; see *Outcome* at the end. Kept as the record of what was
measured and why the idea does not pay.
**Issue:** #154

## Problem

Zero of the 1697 live rules in the vault link to another rule. Every edge in
the vault points from a rule to the briefing that produced it; nothing points
sideways. The vault is 1697 islands.

This surfaced visually — the Obsidian graph renders as hundreds of identical
small stars — but the cost that matters is retrieval. `list_rules_by_topic`
returns a flat bucket ranked by `source_count`, popularity, and a gated BM25F
rerank. When the query's wording misses a rule, nothing else can surface it.

Measured baseline (`.mnemo/recall-report.json`, 71 frozen cases, 2026-09-02):

```
primacy@3   22 (31.0%)
primacy@5   30 (42.3%)
primacy@10  40 (56.3%)
MRR         0.285
misses      31
```

[[recall-degrades-with-topic-size]] records the shape of the failure: primacy@5
falls from 93% to 41% as a topic bucket passes ~20 rules. Ranking within a
bucket was already addressed (#105, the gated rerank); what remains is the case
where the right rule is not in the bucket the query reached, or is lexically
invisible to it.

## Goal

Give retrieval a second path to a rule: when text matching fails, reach it
through a neighbour that text matching found.

Non-goals: a prettier graph (a side effect, not the objective), vault
maintenance tooling (dedup and reclassify already cover it), and any rule↔rule
edge that a human curates by hand.

## Why similarity, and why not the alternatives

**Co-occurrence of reads — rejected on evidence.** The obvious signal is "when
Claude needed A it also needed B", read from `mcp-access-log.jsonl`. Measured:
96 `read_mnemo_rule` calls across four months, 69 distinct pairs when bucketed
into 30-minute per-project windows, and only 2 pairs seen more than once. There
is no signal there. A graph built on it would be noise wearing the shape of
data.

**Same-briefing siblings — rejected on quality.** Free (already in `sources:`)
and high-coverage, but it links by accident of co-extraction: one session that
touched auth and CSS produces an edge between them.

**LLM during extraction — deferred.** The extractor already shows the model up
to 80 existing slugs (`extract/prompts/existing_rules.py`) so it can reinforce
rather than duplicate; asking it to also name related rules is a small prompt
change. Better quality, but it costs per extraction and only covers *new*
rules — the 1667 existing ones would need an expensive backfill. Worth
revisiting if the cheap signal proves the concept.

**Textual similarity — chosen.** `dedup.py` already computes
`weighted_jaccard` over a name/description/body profile, calibrated against
this vault with a documented false-positive list. The machinery exists and is
trusted.

The insight that makes this cheap: the pairs `dedup` *rejects* are the ones we
want. `css-test-file-isolation` ↔ `test-artifacts-must-not-...` scores 0.4452
and is correctly refused as a merge — merging would destroy a page — but it is
a legitimate sibling. That signal is computed and thrown away today.

## Measurements that shaped the design

All figures from the live vault (1667 rules after excluding `_archive` and 30
stray `.proposed` drafts — see Related work).

Full pairwise scan: 1,439,056 pairs in 44s. Batch-viable, query-hostile.

Edges by threshold, capped at the top 3 per rule:

| threshold | rules with an edge | edges | mean degree |
|---|---|---|---|
| 0.22 | 556 (33%) | ~410 | 1.5 |
| 0.25 | 328 (20%) | ~203 | 1.2 |
| 0.28 | 209 (13%) | ~122 | 1.2 |

Reachability of the 31 current recall misses — of the misses, how many have at
least one neighbour at that threshold:

| threshold | misses reachable |
|---|---|
| 0.22 | 18/31 (58%) |
| 0.25 | 11/31 (35%) |
| 0.28 | 9/31 (29%) |

Reachability is a **ceiling, not a gain**. Having a neighbour does not mean the
neighbour is the right rule, nor that expansion fires on that case. The real
improvement is a fraction of these numbers, which is why the prototype gate
below exists.

Sample pairs at 0.22+, from the miss set:

```
0.341  lower-api-poll-rates-to-avoid-detection  /  reduce-watchdog-poll-frequency-16ms-to-1000ms
0.353  prefer-global-hooks-over-per-thread      /  global-hook-is-camuflage-per-thread-is-signature
0.246  reuse-battle-tested-endpoints            /  reuse-existing-ui-components-instead-of-reimpl
0.213  remove-unused-type-fields                /  hardcode-non-essential-form-fields   ← lexical noise
```

The first two are the same idea under different names, which is exactly why
BM25F missed them. The last one shows what the low band costs.

## Design

Three units, each with one job.

### 1. `core/related.py` — computation

Pure. Takes rules as `(slug, name, description, body)`, returns
`{slug: [(neighbour_slug, score), ...]}`. No disk, no vault, no config.
Reuses `weighted_jaccard` and `_weighted_profile` from
`extract/inbox/dedup.py` — imported, not copied.

- **Threshold 0.22.** Below the dedup threshold on purpose: this is not a
  merge, so a wrong edge costs a line at the bottom of a list, not a destroyed
  page. 0.25 discards half the reachable misses to avoid a cost the expansion
  rule already neutralizes. Treat 0.22 as the prototype's starting point, to be
  confirmed or moved by measurement — the same way 0.32 was arrived at for
  dedup.
- **No name-overlap gate.** `dedup` needs it because a false merge is
  destructive. Here it removes the best pairs: `lower-api-poll-rates` ↔
  `reduce-watchdog-poll-frequency` has name overlap 0.21 and is the most useful
  edge in the miss set. The gate would reject precisely the lexically-invisible
  pairs this exists to catch.
- **Cap 2 neighbours per rule.** Mean degree at 0.22 is 1.5, so a cap of 3
  would be dead code. The cap exists to stop a generic rule becoming a hub —
  the failure mode #149 just fixed in `HOME.md`.
- **Symmetric.** A⇄B always written both ways, so expansion does not depend on
  which rule matched first.

### 2. `rule_activation` — storage

Each rule in `rule-activation-index.json` gains
`related: [{"slug": ..., "score": ...}]`. `schema_version` increments.

An index written before this change has no field; readers use
`.get("related", [])`. This mirrors the `manifest.format` compatibility
approach from #143 — an older artifact must not be misread as a newer one with
empty data.

Edges are computed during index construction, not on read.

### 3. `mcp/tools.list_rules_by_topic` — consumption

Expansion runs **after** the existing scope filter, sort and gated rerank —
never inside them.

**Fires only when the result is thin:** the topic bucket returned few rules, or
nothing cleared the BM25F gate of 1.0 (i.e. text matching failed). Exact
condition is a prototype output.

**Behaviour:** for each rule that matched, collect its neighbours, drop any
already present, append at most 3 total at the end, ordered by edge score.

**Never:** reorders, removes, or displaces a rule that matched. A neighbour
only occupies space that was empty.

Neighbours are returned marked (`via: "related"` on the `RuleRef`) so the
harness can measure them separately and the caller can tell a suggestion from a
match.

The timidity is deliberate. `primacy@5` measures whether the right rule is at
the top; anything that pushes good results down damages the metric being
improved. #105 set this precedent — pure BM25F was rejected because it sank
high-quality multi-agent rules on lexical misses.

## Prototype gate

`tools/proto/related_expansion.py` — outside `src/`, not packaged. Computes
edges, replays the 71 frozen recall cases with and without expansion, reports
primacy@3/5/10, MRR and the per-case delta. Sweeps thresholds 0.20 / 0.22 /
0.25 and the firing conditions.

Decision criteria, fixed now so they cannot be rationalized later:

- **≥ +4 cases** on primacy@5 (42.3% → ~48%) with no regression: integrate.
- **+1 to +3 with zero regression:** integrate only if the production code fits
  in roughly 150 lines. Otherwise the maintenance is not worth the gain.
- **Any net regression, or no gain:** discard. Record the number here and close
  the issue.

## Work order

1. Prototype and measure — **gate; work may end here**
2. `core/related.py` and its tests
3. `related` field in the index, `schema_version` bump, old-index compatibility
4. Expansion in `list_rules_by_topic` and its tests
5. Re-measure with the real harness; the number goes in the CHANGELOG

Steps 2–5 exist only if step 1 passes.

## Testing

`related.py` is pure: dictionaries in, edges out. Cases are threshold cutoff,
the per-rule cap, symmetry, and that an empty or single-rule vault produces no
edges.

The expansion is tested mostly on what it does *not* do: a query with a healthy
result set returns byte-identical output; a matched rule never moves; the
appended count never exceeds the cap.

Index compatibility: an index without the `related` field is read as having no
edges, not as an error.

## Risks

**O(n²) growth.** 44s at 1697 rules, roughly 3 minutes at 5000. It runs during
reindex, never per query. Bucketing by topic tag would cut it, but that is
optimization ahead of pain — not now.

**Low-band noise.** 0.22 admits pairs like `remove-unused-type-fields` ↔
`hardcode-non-essential-form-fields`, related only by vocabulary. Accepted
because expansion appends at the end of an already-thin result. If the
prototype shows these actively hurting, the threshold moves up.

**Small expected gain.** Even at best, 58% of misses have a neighbour and only
a fraction convert. This is not a fix for primacy@5 at 42% — it is a small gain
on a subset. The gate exists so that a small gain does not silently become
permanent maintenance.

## Related work

Two findings from the same investigation, both out of scope here and filed
separately:

- **30 `.proposed.md` drafts are being served as live rules** (#155). Files like
  `clubinho__aniversario-e-rastreio.proposed.md` sit beside the real rule,
  pass `is_consumer_visible`, and reach retrieval. They dominate the top of any
  similarity ranking (11 of the top 15 pairs vault-wide) because each is a near
  copy of a live rule.
- Rule frontmatter must be parsed with `filters.parse_frontmatter` +
  `topic_tags`, never a hand-rolled regex. A regex written during this
  investigation swallowed the `activates_on.path_globs` block and produced a
  fabricated tag-hygiene problem (reported as 1399 tags / 65% singletons /
  path globs leaking into tags; the real figures are 357 tags, 29% singletons,
  no leak).


---

## Outcome: rejected

The premise of this design is wrong, and the error is in the Problem section
above: "when the query's wording misses a rule, nothing else can surface it."

Checking `run_case` while writing the implementation plan:

```
cases outside top-5:                     41
  present but buried (rank 6-44):        41
  absent from the result (rank is None):  0
```

**The right rule is never missing.** All 71 frozen cases return it; 41 of them
rank it below 5. The 31 "misses" in `recall-report.json` are `rank > 10`, not
absences. Neighbourhood *expansion* appends rules that are absent, and none are
— so the designed mechanism cannot move `primacy@5` by construction, not by
bad luck.

Two further facts make the original firing condition unreachable: the harness
calls `list_rules_by_topic` with no `query`, so the BM25F gate never runs and
"nothing cleared the gate" is not observable; and topic buckets are large
(median 32, max 89, only 5 cases at ≤5), so "thin bucket" almost never fires.

### The reordering variant, also rejected

If the rule is present but buried, the usable mechanism is the opposite of
expansion: let a well-ranked rule pull its neighbour *up*. Measured directly —
of the 41 buried cases, how many have a neighbour (≥0.22) that the same query
already places in its top 5:

```
4 of 41
```

Four is the ceiling: it assumes every edge is correct and every boost lands.
The spec's own criterion covers this — "+1 to +3 with zero regression:
integrate only if the production code fits in roughly 150 lines." This is three
units plus an index schema migration. It does not fit, and the criterion was
fixed before the number was known precisely so it could be applied rather than
argued with.

### What survives

The edges themselves are real and the pairs are good
(`prefer-global-hooks-over-per-thread` ↔
`global-hook-is-camuflage-per-thread-is-signature` is the same idea under two
names). What died is the theory that retrieval can use them. If rule↔rule
edges are revisited, it should be for a purpose that survives the fact that
retrieval already returns the right rule — the problem is ordering, and
ordering is not short of candidates.

### Method note

The measurement that killed this design was available from the start:
`rank is None` in the existing report. It was not checked until the plan forced
a close reading of `run_case`. The reachability numbers gathered earlier (58%
of misses have a neighbour) are correct and were never relevant.
