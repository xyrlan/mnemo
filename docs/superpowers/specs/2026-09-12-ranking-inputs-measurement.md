# Measurement — the two ranking inputs #158 asks about

Date: 2026-09-12
Issue: #158 ("primacy@5 is a ranking problem: the right rule is returned but ranks 6–44")
Script: `tools/measure_ranking_inputs.py` (read-only; no LLM calls, no writes)
Vault: `~/mnemo`, 1768 rules indexed, access log 2892 entries (189 `list_rules_by_topic`)

#158 closes with "Not proposing a fix. The next step is measurement." This
document is that measurement and nothing more. It answers the issue's two
numbered observations, and reports one thing the issue did not anticipate.

Reproduce:

```
PYTHONPATH=src python3 tools/measure_ranking_inputs.py
```

## (1) Do real callers pass a query? Yes — since 2026-08-24, always.

The issue asked what the 177 logged calls pass. The log now holds 189
(12 more since the issue was filed). The headline split:

| | calls | share |
|---|---:|---:|
| with `query` | 33 | 17.5% |
| without `query` | 156 | 82.5% |

**The headline is misleading and should not be quoted.** The calls are not a
single population — there is a clean cutover:

| era | calls | queried |
|---|---:|---:|
| before 2026-08-24 14:00Z | 161 | 5 (3.1%) |
| from 2026-08-24 14:00Z | 28 | 28 (**100%**) |

The last query-less call is `2026-08-24T12:33:33Z`. All 28 calls after it carry
a query, across 5 distinct projects (clubinho, clearframe, mnemo, sg-imports,
central-inteligencia-frontend) — this is the client behaviour change from #105
propagating, not one project's quirk. Post-cutover query length: 7–23 words,
median 12 (6–23, median 11 if the five earlier queried calls are included).

**Conclusion.** Every caller today passes a query. The pre-#162 harness, which
sent none, was measuring the `source_count`+popularity path that **no live
caller has taken since 2026-08-24**. The issue's conditional — "if (1) shows
real callers usually pass a query, then the harness is measuring a path users
do not take" — is satisfied.

## (2) Is the 42.3% figure void? Yes.

PR #162 (7487f15) landed 2026-09-08 19:46:10 -0300 and made the harness carry
the logged query. The `recall-report.json` found in the vault before this
measurement was generated at `2026-09-08T22:17:56Z` — 19:17:56 -0300, i.e.
**~28 minutes before that commit**. It reported `cases: 2` and
`unqueried: {cases: 0}`, so even that stale file was no longer the 71-case
42.3% run; the 42.3% figure predates both and describes the query-less path.
It is void.

(That file has since been overwritten by the re-run below. The timestamps above
are recorded here because the evidence is not recoverable from the file itself
— `recall-report.json` is last-run-only and is not version-controlled.)

Re-run on current `master` + real vault:

```
cases              : 10
primacy@3 / @5 /@10: 7 / 8 / 9
rate @3 / @5 / @10 : 70.00% / 80.00% / 90.00%
MRR                : 0.5783
with query         : 10 cases, primacy@5 8 (80.00%)
without query      : 0 cases
orphan cases dropped: 74
```

**primacy@5 is 80.0% (8/10), not 42.3%.** Every surviving case is queried, so
the number now describes the path real callers actually take.

**But n=10 carries very little weight, and the reason is not rule churn.**
84 list→read pairs exist in the log; 74 are dropped as orphans (88%). The cause
is a second, separate format cutover the issue did not know about:

| month | `hit_slugs` recorded as names | as slugs |
|---|---:|---:|
| 2026-05 … 2026-08 | 171 | 0 |
| 2026-09 | 2 | 16 |

Last name-shaped row `2026-09-02T16:19:43Z`; first slug-shaped
`2026-09-08T11:56:07Z`. The split against the orphan filter is perfect:

- 74/74 dropped cases have a **name-shaped** `expect_slug` — 0 of them can ever
  match an activation-index key, which is keyed by slug.
- 10/10 kept cases are slug-shaped, and all 10 are present in the index.

So the orphan filter is not detecting renamed or deleted rules. It is
discarding every case bootstrapped from a pre-2026-09-08 log row, because those
rows record a name where the index wants a slug. The measurable case set is
therefore capped at "pairs logged in the last ~4 days", and will stay small
until more slug-era traffic accumulates.

**80% on n=10 is directionally better than 42.3%, but it is not yet a number to
tune against.** A single case moving changes it by 10 points.

## (3) Is `source_count` flat? Yes — worse than the issue recorded.

[[workstream3-root-cause]] recorded 46/48 tied at `source_count=1` in one
bucket. Vault-wide:

| `source_count` | rules | share |
|---:|---:|---:|
| 1 | 1730 | **97.9%** |
| 2 | 33 | 1.9% |
| 3–8 | 5 | 0.3% |

Measured as within-bucket rule *pairs* the sort must order (pairs, not rules,
so buckets of different sizes are commensurable), across all 1021
(project, topic) buckets — 42387 pairs:

| ordering key | pairs it cannot separate |
|---|---:|
| `source_count` | 38769 / 42387 = **91.5%** |
| `source_count` + popularity | 38303 / 42387 = **90.4%** |

Two of the largest real buckets are fully degenerate — `meunu:integration`
(43 rules) and `clubinho:mobile` (41 rules) sit at **100%** tied, every rule at
`source_count=1`. Across the 49 buckets with ≥20 rules the mean tie rate is
91.5%.

The popularity tiebreak recovers **1.1 percentage points**. Part of why is the
same format bug: of 24 slugs with reads in the 30-day window, only 13 match an
index key — the other 11 are name-shaped rows that can never join.

**Conclusion.** For ~90% of pairs the documented sort
(`source_count` → popularity → slug) is decided by **slug, i.e. alphabetical
order**, which carries no relevance signal. The issue's framing is confirmed:
the primary key cannot discriminate, and the ordering falls through to an
arbitrary tiebreak.

## What this does and does not license

Measured and settled:

- Real callers always pass a query (since 2026-08-24). The BM25F rerank path
  gated at `_RERANK_MIN_SCORE = 1.0` **is** the live path.
- 42.3% is void; the current queried number is 80% at n=10.
- `source_count` fails to order ~90% of pairs; alphabetical decides them.

**No ranking change is proposed here, and the measurement does not yet support
one.** The obvious move — lean harder on BM25F, since it is the only key with a
relevance signal — cannot be evaluated right now: with n=10 and an 88% orphan
rate driven by a log-format artifact, the harness cannot distinguish a real
improvement from noise. Tuning against 10 cases would be fitting to the
sampling accident, which is the mistake #154 was discarded for.

The unblocking step is a measurement fix, not a ranking mechanism: make
bootstrap resolve name-shaped `expect_slug` values from the pre-2026-09-08 log
through the vault's name→slug mapping, recovering some of the 74 dropped pairs
into a real case set. Only then is there a baseline worth measuring a ranking
change against — and the thing to measure it against is queried primacy@5 on
that recovered set, reported with its case count, versus the same set under the
current gate.

Two follow-ups worth filing separately, both revealed rather than assumed:

- The access log's name→slug format change silently voids historical recall
  cases and 11/24 popularity entries. Nothing warns; the cases just vanish into
  `orphan_dropped`.
- `source_count` is 97.9% constant vault-wide. Whatever it was intended to
  measure, it is not currently a ranking signal, and keeping it as the primary
  key is what hands ordering to the alphabet.
