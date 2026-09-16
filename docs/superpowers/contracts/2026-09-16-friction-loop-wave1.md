---
feature: friction-loop-wave1
created: 2026-09-16
verdict: parallel
---

Closes the open half of mnemo's learning loop. Two specs carry the reasoning
and must be read before writing any code:

- `docs/superpowers/specs/2026-09-16-friction-ledger-design.md` (subsystem 1)
- `docs/superpowers/specs/2026-09-16-refutation-design.md` (subsystem 2)

**Wave 1 of the friction-loop contract: the `ledger` piece alone.** The full
four-piece contract is
`docs/superpowers/contracts/2026-09-16-friction-loop.md`; `link`, `backfill`
and `retire` all consume the record shape this piece owns, so they are
dispatched only once this has landed on master. Nothing about the other three
is this child's work.

This preamble records the measurements that forced the design and the facts in
the code the piece is written against. It says what it must deliver and where
it may work, never how.

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

## ledger

- **files:** src/mnemo/core/friction/__init__.py, src/mnemo/core/friction/ledger.py, tests/unit/test_friction_ledger.py
- **exposes:** `LEDGER_NAME: str`, `FrictionRecord`, `record(vault_root, rec) -> str | None`, `iter_records(vault_root, *, project=None, since=None) -> Iterator[FrictionRecord]`, `record_id(rec) -> str`
- **consumes:** nothing
- **may:** pr

The on-disk contract for the ledger and nothing else: no LLM, no rule lookup,
no extraction, no writes to any page. Records in, records out.

Deliver:

- `FrictionRecord`: a frozen dataclass with the fields the spec's data model
  lists — `id`, `ts`, `session_id`, `project`, `quote`, `rule_text`,
  `briefing`, `contradicts: list[str]`, `link_basis`, `injected_in_session:
  list[str]`, `origin`, `backfilled: bool`. `link_basis` is one of
  `"extractor"`, `"extractor+injected"`, `"none"`; reject any other value at
  construction, because a downstream consumer branches on it.
- `record`: append one row to `<vault>/.mnemo/friction-ledger.jsonl`, returning
  the id. Follows `briefing-log.jsonl` exactly: same telemetry switch, 1 MiB
  rotation, and **never raises** — a failed write costs a ledger row, never an
  extraction. Decide whether a duplicate `(session_id, quote)` is refused or
  appended, and say which in the docstring; the backfill reruns over the same
  sessions, so this decision is load-bearing.
- `iter_records`: read live file and rotated siblings, newest last, tolerating
  a malformed line by skipping it (a hand-edited ledger must not crash a
  reader). `since` takes a date; `project` an exact name.
- `record_id`: stable and collision-resistant over the record's content, so a
  rerun of the backfill can recognise what it already wrote.

The quote is never re-verified here — the caller does that with
`corrections.verify` before constructing the record. State that boundary in the
module docstring so a later caller does not assume this module checks.

Test: append/read round-trip, rotation at the boundary, a malformed line
skipped, a read-only vault returning `None` rather than raising, and rejection
of an invalid `link_basis`.

---

