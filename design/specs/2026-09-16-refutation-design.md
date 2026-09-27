# Refutation — a contradicted rule stops competing

**Status:** design, approved 2026-09-16
**Subsystem 2 of 3.** Consumes: the friction ledger (subsystem 1). Blocks:
friction-driven extraction (subsystem 3).

## The problem this half solves

Subsystem 1 records that a correction contradicts a rule. Nothing acts on it. A
contradicted rule keeps its rank, keeps being injected, and keeps being shown to
the model through the MCP tools — the ledger is a log nobody reads.

This subsystem is where the vault first **acts on itself**: a rule the user has
contradicted leaves the candidate pool, and the vault shrinks without the
maintainer running anything.

That is also where the risk is. Every other cleanup in mnemo — `reclassify`,
`reverify`, `stale` — is invoked by a human who reads the plan first. This one
runs on its own. The design is built around making that safe: one reversible
action, a full trail, and no deletion.

## What "retired" means

A retired rule is **still on disk, still readable, still linked**. Exactly one
thing changes: it is no longer a candidate for automatic injection or automatic
listing.

| surface | retired rule |
|---|---|
| reflex injection | excluded |
| `list_rules_by_topic` | excluded by default, `include_retired=True` returns it |
| `read_mnemo_rule` by slug | **always returned**, with its retirement stated |
| `mnemo recall` / `replay` | counted separately, never silently dropped |
| the file itself | untouched except for two frontmatter keys |

This split is the whole safety argument. Automatic surfaces stop offering a rule
the user contradicted; deliberate surfaces — someone naming the slug — always
answer, because a wrong retirement must be discoverable by the person who goes
looking.

## Frontmatter

Defined in subsystem 1, written here:

```yaml
superseded_by: mnemo__merge-requires-admin-corrected
superseded_at: 2026-09-16T02:50:04Z
superseded_by_friction: f-20260916-3a7c1e
```

and on the rule that replaced it:

```yaml
supersedes:
  - mnemo__merge-requires-admin
```

`superseded_by_friction` is the ledger id — the trail back to the exact quote the
user typed. A retirement whose ledger record cannot be found is reported by
`mnemo doctor` as inconsistent and is **not** honoured by the gate, so a
hand-edited or corrupted frontmatter key cannot silently retire a rule.

## When a retirement happens

On `mnemo extract`, after the ledger record is written, for each record whose
`link_basis` is `extractor` or `extractor+injected` and whose `contradicts` names
a live rule.

**Both bases retire.** The first draft of this design retired only on
`extractor+injected` — the corroborated case. Rejected on measurement: only 6.5%
of prompts received an injection historically (~22% at `relative_gap` 1.15, #332),
so requiring corroboration would discard the large majority of real contradictions
and make the subsystem inert for months. Corroboration is recorded and reported,
not required.

**A retirement needs a replacement.** The contradicting correction always produces
a rule (`Correction.rule` is the imperative the briefing derived). If for any
reason no page is written from it, the retirement does not happen: a vault that
removes a rule and puts nothing in its place has lost knowledge, which is the one
outcome worse than holding a wrong rule.

## Loop and cascade protection

Three failure modes, each closed explicitly:

**Cycles.** B supersedes A; later A' supersedes B. Allowed — that is the vault
changing its mind twice, which happens. What is refused is a rule superseding
something that already supersedes it *transitively at write time*: the chain is
walked before writing, and a cycle is refused with both slugs named, the way
`mnemo land` refuses a cyclic contract.

**Chains.** A → B → C leaves A and B retired and C live. `mnemo friction` prints
the chain; nothing follows it at query time, because only the live tip is a
candidate.

**Mass retirement.** A malformed contradiction pass could name many slugs at once.
A single extraction run may retire at most `MAX_RETIREMENTS_PER_RUN` (default 5)
rules; beyond that it records the ledger entries, retires nothing, and reports the
overflow. The number is a circuit breaker, not a judgement — the same shape as
#329's five-markers-a-pass lock.

## Components

**`core/friction/retire.py`** — given a ledger record and the vault, write both
frontmatter sides, refuse cycles, honour the per-run cap. The only writer. Returns
a report; never raises into the extraction path.

**`core/reflex/index.py`** — `docs[slug]` gains `retired: bool`, read from
frontmatter the same way `stability` already is. `candidates_for_project` filters
it out. This is the single chokepoint for injection, so the reflex, `replay` and
`mnemo why` all inherit the behaviour from one change.

**`core/mcp/tools.py`** — `list_rules_by_topic` excludes retired rules and states
the count it withheld (`3 retired rules not shown`). `read_mnemo_rule` returns a
retired rule with a header naming what superseded it and the quote that did it.

**`core/friction/report.py`** — powers `mnemo friction`: what is retired, by which
correction, in which chain, and how many retirements were corroborated by an
injection.

**`cli/commands/friction.py`** — gains `--undo <friction-id>` and `--undo-all
--since DATE`, restoring both frontmatter sides. Reversal is a first-class verb,
not a recovery procedure.

**`cli/commands/doctor_checks/`** — one row: retirements whose ledger record is
missing, chains longer than 3, and any cycle found on disk.

## The surfaces that must agree

Measured 2026-09-16, these read live rules and would each need to know about
retirement:

```
core/mcp/tools.py            core/mcp/recall.py         core/mcp/popularity.py
core/mcp/recall_sessions.py  core/reflex/index.py       core/reflex/replay.py
core/extract/prompts/existing_rules.py                  core/dashboard.py
cli/commands/recall.py       core/activity/summarize.py
```

Rather than edit ten call sites, retirement is resolved in **two** places —
`reflex/index.py` for everything ranked, and a `filters.is_retired(fm)` predicate
for everything that reads pages directly. Every surface above either ranks (and so
inherits the index behaviour) or parses frontmatter (and so calls the predicate).
A test enumerates the list and asserts each surface takes one of the two paths, so
a new surface added later cannot quietly ignore retirement.

**`existing_rules.py` deliberately keeps showing retired rules**, marked as
retired. The extractor must know a rule was contradicted — otherwise it mints the
old rule again from a fresh transcript, and the vault re-learns what it just
unlearned. This is the one place where hiding would cause the harm it is meant to
prevent.

## Error handling

- retirement write fails → ledger record stands, one `.errors.log` row, extraction
  continues
- `contradicts` names a slug that no longer exists → recorded, reported, no write
- ledger record missing for an on-disk `superseded_by` → gate ignores the key,
  doctor reports it
- per-run cap exceeded → nothing retired that run, overflow reported
- cycle detected → refused with both slugs named

No path deletes a page, and no path writes outside the two frontmatter keys.

## Testing

- **Unit**: cycle refusal, chain walking, per-run cap, both frontmatter sides
  written and both removed by `--undo`.
- **Unit**: `is_retired` against a page whose ledger record is missing → False.
- **Contract**: the enumerated surface list above; each must rank or call the
  predicate.
- **Integration**: a full extraction run where a correction contradicts a live rule
  — assert the rule leaves `candidates_for_project`, stays readable by slug, and
  still appears in `existing_rules` marked retired.
- **Regression**: `replay` over the real transcripts before and after a retirement
  — the retired rule's injections move to a separate count rather than vanishing,
  so no historical number silently changes.
- **Real-data**: the backfilled ledger applied in dry run over the maintainer's
  vault, reporting how many live rules stand contradicted, with no writes.

## Rollout

`mnemo friction --retire` is **dry run by default**, printing every retirement it
would make with the quote that justifies it. `--apply` executes exactly that list.
Automatic retirement during extraction is off until
`friction.autoRetire` is set to `true` in config; the default ships `false`, so
subsystem 2 lands inert and is switched on once the dry run over the real backfill
has been read by a human.

This is the same shape as `backfill.autoOnFirstSession`, which #234 established as
the pattern for a sweep that changes the vault.

## Success criteria

1. A contradicted rule stops being injected and stops being listed, while staying
   readable by slug.
2. `--undo` restores it exactly, both sides.
3. No surface in the enumerated list ignores retirement, by test.
4. The dry run over the real vault states how many live rules stand contradicted —
   and the maintainer reads that number before `autoRetire` is ever set.
5. Full suite green; `replay`'s historical counts unchanged while `autoRetire` is
   off.

## What this subsystem does not do

- It does not decide what gets extracted. That is subsystem 3.
- It does not delete, archive or rewrite any page body.
- It does not retire on similarity, popularity, age, or `mnemo stale` output —
  only on a ledger record backed by a verified user quote or a CI log.
- It does not act without a replacement rule.

## Related

Subsystem 1 (friction ledger), #332 (why corroboration cannot be required), #329
(per-pass cap shape), #234 (opt-in sweep default), #314 (breaker), #184 (why
`existing_rules` must still show retired rules).
