# Reflex cold-start floor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development, TDD per task.

**Problem (measured 2026-09-03):** BM25F uses Laplace idf `ln((N − df + 0.5)/(df + 0.5) + 1)`. In a vault with N=1 rule a matching term is worth at most 0.29, so the top-1 score for a perfectly matching prompt is 0.4–0.7 and never reaches `absoluteFloor` 2.0. Measured with the demo yarn rule: N=1 → 0.61, N=5 → 1.29, first emission at N≈6 for a strong prompt, N≈11 for a plain one. Every new user's first `mnemo learn` is therefore silent on the next prompt. Distinct from the "index not built yet" cold start.

**Fix:** scale the floor by the vault's maximum attainable idf. With `idf_max(N) = ln((N − 0.5)/1.5 + 1)` (a term present in exactly one of N docs):

```
effective_floor = absolute_floor × min(1, idf_max(N) / idf_max(N_ref))      N_ref = reflex.thresholds.floorReferenceDocs (default 30)
```

N is the index `doc_count`. Vaults with ≥ N_ref docs are unchanged (ratio clamps to 1). N=1 → ×0.095 (floor 0.19); N=6 → ×0.51 (1.02); N=15 → ×0.78. The two other gates (overlap ≥ 2, relative gap) stay as they are, so a tiny vault still needs a real lexical match.

**Files:**

| Path | Change |
|------|--------|
| `src/mnemo/core/reflex/gates.py` | `idf_max(n)`, `effective_absolute_floor(floor, doc_count, reference_docs)`; `evaluate_gates(..., doc_count=None)`; `GateResult.effective_floor` |
| `src/mnemo/hooks/user_prompt_submit.py` | pass `doc_count`; log `absolute_floor_effective`, `floor_reference_docs`, `doc_count` in `thresholds` |
| `src/mnemo/core/config.py` | `reflex.thresholds.floorReferenceDocs: 30` |
| `src/mnemo/core/reflex/receipts.py` | `absolute_floor_fail` explanation uses the effective floor and says when it was scaled |
| `tests/unit/test_reflex_gates.py`, `tests/unit/test_hook_user_prompt_submit.py`, `tests/unit/test_reflex_receipts.py` | new cases |
| `docs/configuration.md`, `docs/getting-started.md`, `CHANGELOG.md` | one row, one sentence, one `### Fixed` bullet |

Out of scope: the calibrator (it proposes the *configured* floor; scaling is applied at decision time), per-project override of `floorReferenceDocs`, any change to scores or the index.

---

### Task 1: gates — effective floor

- [ ] Tests first in `tests/unit/test_reflex_gates.py`:
  - `idf_max(1)` ≈ 0.2877, `idf_max(30)` ≈ 3.03 (use `math.log`, `pytest.approx`).
  - `effective_absolute_floor(2.0, doc_count=1, reference_docs=30)` ≈ 0.19; `doc_count=30` → 2.0; `doc_count=500` → 2.0 (never above configured); `doc_count=0` or `None` → 2.0 (no scaling); `reference_docs<=1` → 2.0.
  - `evaluate_gates([("a", 0.6)], query_tokens=["add","dependencies","package"], doc_tokens_by_slug={"a": {...}}, thresholds=DEFAULT_THRESHOLDS, doc_count=1)` accepts `["a"]`; same call without `doc_count` → `absolute_floor_fail` (backward compatible); `res.effective_floor` reports the value used in both cases.
- [ ] Implement in `gates.py`. `DEFAULT_THRESHOLDS` gains `"floor_reference_docs": 30`. Docstring: the three-gate text plus one paragraph on the scaling and why (idf ceiling grows with N; the floor must not outrun it).
- [ ] Commit: `reflex: scale the absolute floor to the vault's idf ceiling`

### Task 2: hook + config + receipts

- [ ] Config default `floorReferenceDocs: 30` under `reflex.thresholds` in `src/mnemo/core/config.py`.
- [ ] Hook (`user_prompt_submit.py`): `gate_thresholds["floor_reference_docs"] = int(thresholds.get("floorReferenceDocs", 30))` (project override key `floor_reference_docs` if present in `overrides`, else config); call `evaluate_gates(..., doc_count=int(index.get("doc_count", 0)))`; before logging (both the silence and the emission paths) add to the thresholds dict: `absolute_floor_effective = result.effective_floor`, `doc_count`. Keep `absolute_floor` as the configured value (existing test asserts it).
- [ ] Hook tests (`tests/unit/test_hook_user_prompt_submit.py`, use the existing `tmp_vault`/`synthetic_index`/`_enable_reflex`/`_run_hook` helpers — read the file first): (a) with a one-rule index and a prompt that overlaps it on ≥ 2 terms, the hook **emits** under default thresholds (this is the cold-start case); (b) the logged entry carries `thresholds.absolute_floor == 2.0`, `thresholds.absolute_floor_effective < 2.0`, `thresholds.doc_count == 1`; (c) `floorReferenceDocs: 1` in config disables scaling (entry `absolute_floor_effective == 2.0`, silence `absolute_floor_fail`). Build the one-rule index the way `synthetic_index` does but with a single page — read the fixture to see how; add a small helper or fixture parameter rather than duplicating it.
- [ ] Receipts (`receipts.py`, `_explain` for `absolute_floor_fail`): floor = `thresholds.get("absolute_floor_effective", thresholds.get("absolute_floor", 2.0))`; if `absolute_floor_effective` is present and lower than `absolute_floor`, append ` (floor scaled down from {configured} — the vault has {doc_count} rules)`. Test in `tests/unit/test_reflex_receipts.py` (read the file for the entry shape): scaled entry mentions "scaled down from 2.0" and the doc count; un-scaled entry (no `absolute_floor_effective`) renders exactly as before.
- [ ] Commit: `reflex: apply the scaled floor in the hook, explain it in mnemo why`

### Task 3: docs

- [ ] `docs/configuration.md` threshold table: new row after `absoluteFloor`: `| \`reflex.thresholds.floorReferenceDocs\` | \`30\` | Below this many rules the floor is scaled down with the vault's idf ceiling, so a young vault can inject at all |`. Amend the `absoluteFloor` meaning to `Minimum score to inject at all (scaled down in small vaults, see next row)`.
- [ ] `docs/getting-started.md` ~line 352, after "…and an absolute floor.": add one sentence: `The floor scales with the vault's size, so the first rule you learn can fire on the next prompt instead of waiting for the vault to grow.`
- [ ] `CHANGELOG.md` `[Unreleased]`: add a `### Fixed` section (after `### Added`, before `## [1.2.0]`) with: `- **Reflex cold start.** A vault with a handful of rules could never clear \`absoluteFloor\` — BM25 idf tops out near 0.3 per term at one rule, 1.3 at five — so the first rule learned never fired on the next prompt. The floor now scales with the vault's idf ceiling below \`reflex.thresholds.floorReferenceDocs\` (default 30); \`mnemo why\` shows both the configured and the effective floor.`
- [ ] Commit: `docs: cold-start floor scaling`

### Verification

- Full suite green.
- Empirical: temp vault with one yarn rule, `evaluate_gates(doc_count=1)` accepts for `add the lodash package to the dependencies` (score 0.61) and for `add lodash as a dependency` (0.37); rejects `install lodash` (overlap 1).
