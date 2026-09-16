---
feature: friction-loop-wave2
created: 2026-09-16
verdict: parallel
---

Closes the open half of mnemo's learning loop. Two specs carry the reasoning
and must be read before writing any code:

- `docs/superpowers/specs/2026-09-16-friction-ledger-design.md` (subsystem 1)
- `docs/superpowers/specs/2026-09-16-refutation-design.md` (subsystem 2)

**Wave 2 of the friction-loop contract.** Wave 1 landed the `ledger` piece on
master (PR #338): `mnemo.core.friction.ledger` already exposes `LEDGER_NAME`,
`FrictionRecord`, `record`, `iter_records` and `record_id`. Read that module
before writing anything — it is the record shape all three pieces here consume,
and it is already fixed. Do not redefine it, and do not change it without
saying so in the PR.

The three pieces below are independent of each other and run in parallel.

This preamble records the measurements that forced the design and the facts in
the code every piece is written against. Each piece says what it must deliver
and where it may work, never how.

**The problem, measured on the maintainer's vault 2026-09-16.** 1946 live
rules; **41 (2.1%)** have more than one source; median sources per rule is 1.
`mnemo replay` over 2691 prompts reports **0** carried correction-backed
injections at every `relative_gap` value tested. Rules promoted by month: 9,
18, 48, 81, 191. The 2026-09-01 product audit recorded the same 98%
single-source figure at 1648 rules — a year and 298 rules later it has not
moved, because nothing in the system acts on it.

**The signal exists and is discarded.** Of 398 briefings on disk, 42 carry a
`## Corrections` section and **100% of those are non-empty** — 70 items, 1.67
per briefing, ~8.75/day over 09-08 → 09-15. The other 356 predate the prompt
that asks for corrections: they are unasked sessions, not frictionless ones.
Today each correction becomes a line of briefing prose and dies there.

**Contradiction is resolved in prose, not structure.** The vault's
`merge-requires-admin` page carries `**CORRECTED 2026-09-12**` inside its body.
A human reader sees it; the ranker does not.

**The link must be semantic, and the existing hint cannot carry it.**
`existing_rules` shows at most `MAX_ENTRIES = 80` rules ordered by
`source_count`: measured, that is **37.9%** of mnemo's eligible `reference`
pool and **23.5%** of clubinho's. Since 97.9% of rules have `source_count = 1`,
the top-80 cut lands inside a tie of 122 (mnemo) or 84 (clubinho) and is
settled by slug order. A contradiction question asked against that sample would
produce a ledger whose silence means nothing. Lexical similarity is also ruled
out: this vault scores a true cross-type duplicate at 0.136, below the p90 of
unrelated noise (0.131), and `merge-requires-admin` contradicts its successor
in near-identical vocabulary. So contradiction gets its own LLM pass, over
candidates ranked against the correction text.

**Corroboration cannot be required.** Only 6.5% of prompts historically
received a reflex injection (~22% at `relative_gap` 1.15, #332). Retiring only
on `extractor+injected` would discard most real contradictions. Corroboration
is recorded and reported, never required.

**Facts in the code every piece is written against:**

- `core.corrections.Correction(quote, rule)` and `corrections.verify` already
  exist and already check mechanically that a quote is a substring of something
  the user typed. A fabricated quote must never reach the ledger; reuse them.
- `ci_corrections.ORIGIN_USER` / `ORIGIN_CI` already name the two channels.
  The CI half (#272) writes to the same ledger.
- `reflex/index.py` builds `docs[slug]` with `stability` read from frontmatter;
  `retired` is read the same way, at the same place.
- `candidates_for_project` is the single chokepoint for injection, so the
  reflex, `replay` and `mnemo why` all inherit a filter applied there.
- Ten modules read live rules today: `core/mcp/tools.py`, `core/mcp/recall.py`,
  `core/mcp/popularity.py`, `core/mcp/recall_sessions.py`, `core/reflex/index.py`,
  `core/reflex/replay.py`, `core/extract/prompts/existing_rules.py`,
  `core/dashboard.py`, `cli/commands/recall.py`, `core/activity/summarize.py`.
  Every one either ranks (and inherits the index filter) or parses frontmatter
  (and must call the predicate). No third path.
- `filters.is_consumer_visible` is the existing precedent for "location and
  frontmatter decide visibility"; `is_retired` sits beside it.
- Telemetry files follow `briefing-log.jsonl` (channels): own file, same
  switch, 1 MiB rotation, never raises.
- `#329` established the shape of a per-pass cap; `#234` established the shape
  of a sweep that ships opt-in.

---

## link

- **files:** src/mnemo/core/friction/candidates.py, src/mnemo/core/friction/link.py, src/mnemo/core/extract/prompts/templates/contradiction.py, tests/unit/test_friction_link.py
- **exposes:** `CONTRADICTION_CANDIDATES: int`, `rank(vault_root, correction, *, project) -> list[Candidate]`, `resolve(correction, candidates, *, runner=None) -> LinkResult`, `CONTRADICTION_PROMPT: str`
- **consumes:** nothing
- **may:** pr

Imports the ledger's record shape from master (`mnemo.core.friction.ledger`);
nothing in this contract owns it.

The judgement, and only the judgement. Does not write the ledger, does not
retire anything, does not touch the consolidation prompt.

Deliver:

- `rank`: score the **whole** eligible pool for `project` against the
  correction's quote and rule text, using `core.reflex.bm25` and the existing
  reflex index — add no new ranking code and do not copy BM25F. Return the top
  `CONTRADICTION_CANDIDATES` (default 40) as `Candidate(slug, name, body,
  score, rank)`, bodies included. "Eligible" is what
  `candidates_for_project` already means. The preamble's measurement is the
  reason this ranks the whole pool and not a `source_count` slice; a test must
  pin that a rule outside a `source_count`-ordered top-80 can still be returned.
- `CONTRADICTION_PROMPT` and its response schema: given the user's verbatim
  quote, the rule the briefing derived from it, and the candidate rules, name
  the slugs the correction contradicts — or none. The prompt must distinguish
  *contradicts* (the new rule says the opposite) from *refines* and from
  *unrelated*; only the first counts. A slug the model returns that is not in
  the candidate list is dropped by the caller, so the prompt does not need to
  defend against it, but say so.
- `resolve`: run the pass and return `LinkResult(contradicts: list[str],
  link_basis: str, raw: dict)`. `runner` is injected so tests never spawn a
  model; the default runs the same `claude --print` path extraction already
  uses, under `MNEMO_HOOKS_OFF=1` (#329). A failure, timeout or malformed
  response yields `contradicts: []` and `link_basis: "none"` — never an
  exception, and never a lost correction.
- `link_basis` is `"extractor+injected"` when a returned slug also appears in
  the caller-supplied injected list, `"extractor"` otherwise, `"none"` when
  nothing was returned. The caller supplies the injected slugs; reading the
  reflex log is not this piece's job.

Test with a recorded fixture runner: the `merge-requires-admin` case (semantic
contradiction, near-identical vocabulary) must resolve where lexical similarity
would not; a refinement must not; a malformed response must degrade to `none`.

---

## backfill

- **files:** src/mnemo/core/friction/backfill.py, src/mnemo/cli/commands/friction.py, src/mnemo/cli/parser.py, tests/unit/test_friction_backfill.py
- **exposes:** `plan(vault_root, *, since=None, project=None, fresh=False) -> BackfillPlan`, `apply(vault_root, plan) -> BackfillReport`, `cmd_friction`
- **consumes:** `rank(vault_root, correction, *, project) -> list[Candidate]` from link, `resolve(correction, candidates, *, runner=None) -> LinkResult` from link
- **may:** pr

Recovers the history the vault threw away, and owns the `mnemo friction`
command surface.

Deliver:

- `plan`: sweep **every session with a transcript on disk**, newest first — not
  only the 356 unasked briefings. `replay` already reads 395 sessions and
  `core.mcp.recall_sessions._transcripts_by_session` already discovers them;
  reuse that discovery. Restricting the sweep to briefed sessions would inherit
  the sampling bias this whole contract exists to remove. For each session:
  re-brief into a scratch root through the ordinary path (one Haiku call,
  reused on a rerun unless `fresh`), parse `## Corrections`, verify each quote
  with `corrections.verify`, then run `rank` + `resolve`. Same shape as
  `mnemo reverify` (#257) — read that command before writing this one.
- `BackfillPlan` is serialisable to `.mnemo/friction-backfill-plan.json` and
  prints per session: *corrections found (N)*, *none found*, *transcript gone*,
  *briefing failed*. A missing transcript is reported and never fabricated.
- `apply`: write exactly what the plan holds, with `backfilled: true` and **no
  second briefing or contradiction pass**. A rerun must not duplicate rows —
  this is where `record_id` earns its keep.
- `mnemo friction` with no flags reports the ledger: totals by project and
  origin, how many live rules stand contradicted, the rank distribution of
  confirmed links (so `CONTRADICTION_CANDIDATES` can be revisited against
  evidence), and how many links were corroborated by an injection. Read-only.
  `--backfill` plans, `--apply` executes, `--json` for both.

Cost is expected and accepted: one briefing call per session over ~395
sessions. `--since` and `--project` bound a run, and the sweep must be
interruptible without losing what it already planned.

Test: a fixture vault with three sessions (one with corrections, one without,
one whose transcript is gone) produces the three outcomes; `apply` twice writes
each row once.

---

## retire

- **files:** src/mnemo/core/friction/retire.py, src/mnemo/core/filters.py, src/mnemo/core/reflex/index.py, src/mnemo/core/reflex/decide.py, src/mnemo/core/mcp/tools.py, src/mnemo/core/extract/prompts/existing_rules.py, tests/unit/test_friction_retire.py, tests/unit/test_retired_surfaces.py
- **exposes:** `MAX_RETIREMENTS_PER_RUN: int`, `retire(vault_root, rec, *, replacement) -> RetireResult`, `undo(vault_root, friction_id) -> RetireResult`, `is_retired(fm, *, vault_root=None) -> bool`, `plan_retirements(vault_root, *, since=None) -> list[RetirePlan]`
- **consumes:** nothing
- **may:** nothing

Reads the ledger from master (`mnemo.core.friction.ledger.iter_records`);
nothing in this contract owns it. The only piece that writes to a vault page. Ships **inert**: automatic
retirement is off until `friction.autoRetire` is `true` in config, default
`false`, following #234.

Deliver:

- `retire`: write `superseded_by`, `superseded_at` and `superseded_by_friction`
  on the contradicted page, and `supersedes` on the replacement. Nothing else
  about either page changes — no body edit, no deletion, no archive move.
  Refuses, with both slugs named, when the write would create a cycle
  (walk the chain first, the way `mnemo land` refuses a cyclic contract).
  Refuses when there is no replacement page: a vault that removes a rule and
  puts nothing in its place has lost knowledge.
- `MAX_RETIREMENTS_PER_RUN` (default 5): beyond it, retire nothing that run and
  report the overflow. A circuit breaker, not a judgement (#329's shape).
- `is_retired`: the single predicate. **A `superseded_by` key whose
  `superseded_by_friction` id is not in the ledger returns `False`** — a
  hand-edited or corrupted frontmatter key must not retire a rule. When
  `vault_root` is `None` the ledger cannot be consulted; decide what that means
  and state it in the docstring, because callers that only have frontmatter
  will use it.
- `docs[slug]["retired"]` in `reflex/index.py`, read from frontmatter as
  `stability` already is, and filtered in `candidates_for_project`. That one
  change must be what removes a retired rule from injection, `replay` and
  `mnemo why` — do not filter in three places.
- `list_rules_by_topic` excludes retired rules and states the count withheld
  (`3 retired rules not shown`); an `include_retired` parameter returns them.
  `read_mnemo_rule` by slug **always** returns a retired rule, with its
  retirement and the quote that caused it stated at the top.
- `existing_rules_fragment` **keeps listing retired rules, marked retired.**
  Hiding them makes the extractor mint the old rule again from a fresh
  transcript, and the vault re-learns what it just unlearned (#184). This is
  deliberate; say so in the code.
- `plan_retirements`: what `mnemo friction --retire` prints in dry run, each
  line carrying the quote that justifies it. The `--retire` / `--apply` /
  `--undo` command surface is added to the `friction` command the backfill
  piece owns; coordinate on the parser entry rather than adding a second one.

`tests/unit/test_retired_surfaces.py` enumerates the ten modules the preamble
lists and asserts each either ranks through the index or calls `is_retired`, so
a surface added later cannot quietly ignore retirement.

Test: cycle refused; cap enforced; both frontmatter sides written and both
removed by `undo`; a retired rule absent from `candidates_for_project`, present
in `read_mnemo_rule`, present and marked in `existing_rules_fragment`; a
`superseded_by` with no ledger record ignored; `replay`'s counts unchanged while
`autoRetire` is off.

---

**Landing order.** `ledger` owns the record shape the other three consume, so it
lands first. `link`, `backfill` and `retire` are independent of each other —
`retire` depends on the record shape, not on `link` — and may be dispatched
together once `ledger` is on master.

`retire` carries no publish grant: it is the only piece that writes to a vault
page, and the maintainer reads its diff before it reaches a branch.

`mnemo land` checks every `exposes` on the merged tree before any PR is merged,
which is the point of writing this as a contract: `retire` is written against a
`FrictionRecord` that must actually exist.
