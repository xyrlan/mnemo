# `_inbox` staged rewrites: reconcile, classify, merge (#159)

Date: 2026-09-12
Issue: #159
Status: design, awaiting approval

## The problem, restated

#159 is filed as a review-ergonomics gap: 33 staged `.proposed.md` rewrites with no
review surface, and reviewing them costs more than ignoring them.

Measurement says it is a correctness leak with a review gap on top.

`shared/_inbox/` holds 35 rewrites (33 at filing, 35 on 2026-09-12). Every one targets
a live rule. `filters.is_consumer_visible` excludes everything under `shared/_inbox/`,
so none of their content reaches recall. The live rules they would correct are served
instead, and three of them are now factually wrong:

| Rule | Live rule (served today) | Staged rewrite (withheld) |
|---|---|---|
| `clubinho__app-marketplace-disabled` | `MARKETPLACE_ENABLED = false`; "não existe mais aba Loja" | `= true`, liberada geral; Loja entry points added 24/08 |
| `meunu__stripe-webhook-eventos-faltantes` | "só escuta 2 dos 5 eventos — bloqueia assinante em dia" | "RESOLVIDO 2026-08-11 (issue #285)" |
| `clubinho__expo-run-ios-quebrado` | "quebrado", tela branca, `ExpoAsset` | "voltou a funcionar (05/08); a memória antiga está obsoleta" |

The cost of ignoring is not zero and never was. It is invisible because the loss happens
in a filtered directory.

## Root cause of the regenerating queue

Four of five staging sites share one comparison: `content_hash(live) != entry.written_hash`
→ stage a `.proposed.md`.

1. Extractor writes a rule, records `written_hash = H1`.
2. A human edits the rule; disk becomes `H2`.
3. Next run sees `H2 != H1`, concludes "user edited", stages a rewrite.
4. Nobody accepts. Promotion is a manual `mv` that updates no ledger, so the live file
   stays `H2` and `written_hash` stays `H1`.
5. Every subsequent run repeats step 3, blind-overwriting the unread draft.

Verified: 35/35 proposals have `written_hash != content_hash(live)`. `written_at` spans
2026-05 to 2026-09, so these rules were hand-edited long ago and have been re-proposing
since.

`written_hash` is only advanced when the extractor itself writes the sacred file
(`StateEntry.mark_written`, `scanner.py:81-87`). No accept path exists to advance it.

### Staging sites (all five lack idempotence)

| Site | Condition | Result key |
|---|---|---|
| `branches/upgrade.py:24-41` | multi-source re-emission of an `auto_promoted` rule | `upgrade_proposed` |
| `branches/universal_promotion.py:106-110` | `disk_hash != entry.written_hash` | `sibling_bounced` |
| `branches/auto_promoted.py:128-132` | `disk_hash != entry.written_hash` | `sibling_bounced` |
| `branches/inbox_flow.py:119-122` | `disk_hash != entry.written_hash` | `sibling_proposed` |
| `branches/inbox_flow.py:124-130` | `_inbox` target gone, promoted file present | `update_proposed` |

None reads the existing `.proposed.md` before `atomic_write`. An unreviewed proposal is
silently replaced by the next run's equivalent.

### The set is closed, not growing

Rebuilding the dirty set the way `scanner.scan()` actually does it — iterate source files
under `bots/*/memory/` and `bots/*/briefings/sessions/`, derive `type/slug` from the file,
compare that key's `source_hash`:

- 325 memory files scanned, 144 dirty
- 28 of those have a live rule
- 28/28 already have a staged proposal

Zero live+dirty rules are missing a proposal. The pile restamps in place on each run; it
does not accumulate. Apparent growth (33 → 35) is mtime churn plus two genuinely new
rules, not a queue filling faster than review.

### Not a cause: prompt blindness

`prompts/existing_rules.py` was a suspected second driver. It is not.
`existing_rules_fragment` renders only `- <slug> — <name>` lines (`existing_rules.py:93`).
The LLM never sees rule bodies, so it cannot be re-deriving a rewrite by comparing against
stale live text. Line 62 skipping `.proposed.md` siblings is correct — it avoids
advertising a draft slug as canonical.

### Separate issue: vault-wide hash drift

1,638 state entries have `written_hash != content_hash(live)`, of which only 35 have
proposals. Cause: the slug-stamping migration. `.mnemo/slugs-stamped.v1` is dated
2026-09-02 20:23; 1234 live pages have that mtime and 1234/1234 carry a `slug:` key.
`migrations/slugs.py:112` rewrites each page with `atomic_write_bytes` and never touches
`written_hash` (zero mentions in the module).

This is inert today: those rules' sources are no longer scanned, so they never re-stage.
It is out of scope here and gets its own issue — the fix belongs at the migration site, so
the next bulk rewrite does not repeat it.

## Accept cannot be a single operation

Classifying each proposal by `difflib` opcodes over the body (frontmatter excluded), and
by the fraction of live non-blank lines the proposal preserves:

| Class | Count | Definition | Safe operation |
|---|---|---|---|
| `INSERT_ONLY` | 11 | opcodes ⊆ {insert}; keep = 1.0 | auto-merge, provably lossless |
| `MIXED` | 18 | keep 0.05–0.97 | needs a decision |
| `FULL_REWRITE` | 6 | keep = 0.0 | needs a decision |

`INSERT_ONLY` (all `project`): `clubinho__aniversario-e-rastreio`,
`clubinho__app-activation-report`, `clubinho__checkout-referral-ux`,
`clubinho__prod-deploy-mechanics`, `clubinho__prod-migrations-drift`,
`meunu__google-ads-campaign-state`, `meunu__pix-key-qr-normalization`,
`meunu__som-alarme-pedidos`, `meunu__stripe-webhook-eventos-faltantes`,
`mnemo__recall-degrades-with-topic-size`, `pedrolobato__terralogs-frontend-only`.

`FULL_REWRITE`: `clubinho__app-marketplace-disabled`,
`clubinho__backend-suite-falhas-preexistentes`, `clubinho__eas-ota-not-configured`,
`clubinho__expo-run-ios-quebrado`, `clubinho__upgrade-plano-app`,
`mnemo__dogfood-standing-gaps`.

Why one semantic cannot serve all three:

- A plain `mv` (overwrite) on the 11 `INSERT_ONLY` cases is unnecessary but harmless for
  the body — yet it drops live `sources[]` entries (see below).
- A plain append on the 6 `FULL_REWRITE` cases produces a rule asserting both
  `MARKETPLACE_ENABLED = false` and `= true`. Worse than the current silence.
- The 18 `MIXED` cases interleave reworded prose with new facts. Example
  (`clubinho__app-marketplace-disabled`, from the diff): live line 4 documents "não existe
  mais aba Loja"; the proposal replaces it with five lines listing the new Loja entry
  points. Appending keeps a contradiction; overwriting is right here but not universally.

## Frontmatter merge rules

Counts are `<differs in the 11 INSERT_ONLY pairs>` · `<differs across all 35>`.

| Key | Differs | Policy | Why |
|---|---|---|---|
| `sources[]` | 11/11 · 33/35 | normalize, then **union** | Across the 11 auto-merge pairs every delta is purely the `/Users/xyrlan/mnemo/` absolute-path prefix (#163, fixed v1.3.3) — these proposals predate the fix. But 3 cases are live=2 → prop=1 (`prod-deploy-mechanics`, `recall-degrades-with-topic-size`, `terralogs-frontend-only`), so proposal-wins drops a source. Union after normalization through `extract.source_paths.vault_relative_source`. |
| `description` | 5/11 · 22/35 | **proposal wins** | Live descriptions are factually stale: `stripe-webhook` live says "bloqueia assinante em dia", proposal says "RESOLVIDO 2026-08-11"; `google-ads` live says "pausada + tracking quebrado", proposal says "Search ENABLED TARGET_SPEND R$50/d". One case (`prod-migrations-drift`) differs only by lost YAML quoting — a rendering artifact, not content, so compare unquoted scalars. |
| `promoted_at`, `extraction_run`, `extracted_at`, `last_sync` | 11/11 · 35/35 | proposal wins, then **overwritten by apply** | Run stamps. `apply` sets `written_at`/`last_sync` to its own `run_id` regardless. |
| `name` | 0/11 · 3/35 | proposal wins | Never diverges inside the auto-merge set. |
| `tags` | 0/11 · 5/35 | **union of topic tags; managed markers never copied from the proposal** | Never diverges inside the auto-merge set, so it does not affect `--apply-safe`. Outside it the proposal's topic tags are usually better (`frontend-gotchas`: live `workflow, testing` → proposal `react, testing, ui, css`). But `tdd-red-green-per-feature` flips `auto-promoted` → `needs-review`, and copying that would re-mark a reviewed rule as a draft. Take the union of `filters.topic_tags(...)` from both sides and preserve the **live** page's `MANAGED_TAGS` marker. |
| `confidence`, `demoted_from`, `activates_on` | 0/11 · ≤2/35 | live wins | Outside the auto-merge set only. `activates_on` drives rule activation and `demoted_from` records a reclassify decision; neither is the extractor's to revise from a transcript. Flag any divergence in `--show` rather than silently changing it. |

## Design

### Component 1 — `core/rewrites/classify.py`

Pure, no I/O beyond reading the two files.

```
@dataclass(frozen=True)
class Rewrite:
    proposal: Path
    live: Path
    key: str                 # "<type>/<slug>", the state-entry key
    kind: Literal["insert_only", "mixed", "full_rewrite"]
    keep_ratio: float        # fraction of live non-blank lines preserved
    inserted_lines: int
    dropped_lines: int

def classify(vault_root: Path) -> list[Rewrite]
```

Body comparison uses `difflib.SequenceMatcher` over body lines with frontmatter stripped
via `reclassify_types.split_frontmatter`. `kind` is `insert_only` when the non-equal
opcode set is a subset of `{"insert"}`; `full_rewrite` when `keep_ratio == 0.0`;
`mixed` otherwise.

Dependencies: `filters.is_proposed_sibling`, `filters.INBOX_DIR`, `split_frontmatter`.

### Component 2 — `core/rewrites/merge.py`

```
def merge_insert_only(live_text: str, proposal_text: str, *, vault_root: Path) -> str
def replace_wholesale(live_text: str, proposal_text: str, *, vault_root: Path) -> str
```

Both rebuild frontmatter per the table above (union `sources[]` after normalization
through `extract.source_paths.vault_relative_source`; proposal wins on scalar keys) and
differ only in
body handling: `merge_insert_only` takes the proposal body (it is a superset — opcodes are
insert-only, so every live line survives in order); `replace_wholesale` also takes the
proposal body but is the explicit, named choice for `full_rewrite`.

Keeping them as two functions rather than one with a flag makes the call sites state which
semantic they intend, which is the thing that was previously implicit in a manual `mv`.

### Component 3 — `core/rewrites/apply.py`

```
@dataclass
class ApplyPlan:
    entries: list[tuple[Rewrite, Literal["merge", "replace", "skip"]]]
    run_id: str

def plan(vault_root: Path, *, include: set[str]) -> ApplyPlan
def apply(plan: ApplyPlan, vault_root: Path) -> ApplyReport
def undo(vault_root: Path, run_id: str) -> int
```

`apply` mirrors `reclassify_apply` exactly, because the vault is not a git repository and
only 1 of 35 proposals has any archive coverage today — there is no other rollback path:

1. Create `shared/_archive/rewrites-<run_id>/` with `originals/`
   (`mkdir(parents=True, exist_ok=True)`), but first raise `RuntimeError` if
   `shared/_archive/rewrites-<run_id>/manifest.json` already exists. The guard is on the
   **manifest**, not on the directory — same as `reclassify_apply.py:151-155`
   (`if (arch / "manifest.json").exists(): raise RuntimeError(...)`). Re-applying a run
   would re-copy already-modified files over the pristine originals and overwrite the
   manifest, silently destroying undo.
2. Copy each live rule's pristine bytes to `originals/<slug>.md`, and back up
   `.mnemo/extraction-state.json` to `originals/extraction-state.json`.
3. Write `manifest.json`: `{run_id, created_at, moves: [{key, slug, kind, action, live_path,
   proposal_path}], skipped, state_backup}` — the shape `undo()` reads.
4. For each entry: write the merged text to the live path via `atomic_write`, delete the
   `.proposed.md`, and **reconcile the ledger** —
   `entry.written_hash = content_hash(merged_text)`, `entry.written_at = run_id`,
   `entry.last_sync = run_id`. This is the step that stops regeneration; without it the
   merged result is itself a "user edit" and re-proposes on the next run.
5. `atomic_write_state`.

`undo` restores `originals/` byte-for-byte plus the state file, matching
`reclassify_apply.undo`.

### Component 4 — `cli/commands/rewrites.py`

`@command("rewrites")`, registered in `cli/commands/__init__.py` and added to
`ADVANCED_COMMANDS` in `parser.py` alongside `dedup-rules` and `reclassify`.

The name avoids `mnemo autopilot proposals {list,review}`, which already exists and reads
a different thing: `.mnemo/proposals/*.json`, 42 files of `kind: rule_candidate` from
`tier0.miss_collector` / `tier3.eos_extractor`. A top-level `mnemo proposals` would be a
near-collision on an unrelated surface.

Dry-run by default, following `dedup-rules`:

```
$ mnemo rewrites
35 staged rewrites in shared/_inbox/

  safe to merge (11) — insert-only, no live content dropped
    clubinho__prod-deploy-mechanics          +4 lines
    meunu__pix-key-qr-normalization          +1 line
    ... 9 more

  needs a decision (24)
    clubinho__app-marketplace-disabled    full rewrite  keeps 0%  ⚠ live rule contradicts proposal
    tdd-red-green-per-feature             mixed         keeps 20%
    ... 22 more

(dry-run — `mnemo rewrites --apply-safe` merges the 11; `--show <slug>` prints one diff)
```

Flags: `--apply-safe` (merge the `insert_only` set), `--show <slug>` (full diff for one),
`--accept <slug>` / `--reject <slug>` (single explicit decision on a `mixed` or
`full_rewrite`), `--undo <run_id>`.

`--apply-safe` is deliberately not `--apply`: an unqualified apply over a set that
includes 6 truth inversions is the mistake this design exists to prevent.

## Testing

TDD per `superpowers:test-driven-development` — test first, watch it fail, then implement.
Baseline to hold: 2594 passed, 2 skipped, 8 deselected (measured 2026-09-12).

`tests/unit/test_rewrites_classify.py`
- insert-only fixture → `kind == "insert_only"`, `keep_ratio == 1.0`
- replace-in-middle fixture → `mixed`
- disjoint-body fixture → `full_rewrite`, `keep_ratio == 0.0`
- proposal with no live counterpart → excluded
- blank-line-only delta → `insert_only` with `inserted_lines == 0`

`tests/unit/test_rewrites_merge.py`
- `sources[]` union: live has 2, proposal has 1 absolute-path form → result has 2, both
  vault-relative (the `prod-deploy-mechanics` case)
- `description`: proposal wins
- insert-only merge preserves every live body line
- merge output is byte-identical when run twice (idempotent)

`tests/unit/test_rewrites_apply.py`
- apply reconciles `written_hash` to `content_hash(merged)` — the anti-regeneration test:
  after apply, a second `plan()` yields no entry for that key
- `originals/` holds pristine live bytes; `manifest.json` matches the documented shape
- `undo` restores bytes and state exactly
- refuses to run when `shared/_archive/rewrites-<run_id>/` exists
- `.proposed.md` is removed on success, left in place on failure

## Out of scope

- The 1,638-entry vault-wide hash drift from the slug-stamp migration (separate issue;
  inert, and the fix belongs at the migration site).
- Idempotence guards at the five staging sites. Once accept reconciles `written_hash`, an
  accepted rule stops re-proposing, which removes the pressure. Blind-overwrite of an
  unread draft remains theoretically lossy and is worth a follow-up, but with content
  re-derived from the same sources each run it loses nothing in practice.
- Rule compaction. Merged rules grow; nothing prunes them yet.
- Any change to `is_consumer_visible`. `_inbox` staying invisible is correct — the fix is
  to empty it, not to expose drafts to recall.

## Open questions for review

1. **Should the 6 `FULL_REWRITE` cases auto-apply?** Their live rules are wrong *now*
   (`MARKETPLACE_ENABLED`, Stripe webhook, expo build), so every day they wait has a
   measurable cost, and `replace_wholesale` plus the archive makes the action reversible.
   The spec currently routes them through a per-rule decision. Auto-applying them would
   drain 17 of 35 in one command instead of 11.
2. **Does `mnemo rewrites` need an interactive per-rule flow**, or is
   `--show` / `--accept` / `--reject` on a printed plan enough? The plan-then-flag shape
   matches `dedup-rules` and needs no new interaction model.
