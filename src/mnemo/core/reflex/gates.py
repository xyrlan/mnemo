"""Confidence gates for Reflex.

Silence is the default; emission requires these to pass against the top-1
candidate:

  (a) term-overlap >= term_overlap_min across the UNION of indexed fields
  (b) relative gap  s[0] >= relative_gap * s[1]   (or s[1] == 0) — OFF by
      default (relative_gap 1.0), see below
  (c) absolute floor s[0] >= absolute_floor

If top-1 passes, top-2 is included ONLY IF it ALSO passes (a) and its score
clears the absolute_floor. Relative gap is never re-checked on top-2 — the
purpose of top-2 is "nearly as good as top-1, not worth hiding."

Why (b) is off (#332). Gate (b) assumed a near-tie between the top two
rules means an *ambiguous* prompt, and silenced the whole prompt on it — the
same condition under which top-2 is admitted as "nearly as good". Replayed
over the maintainer's vault (2723 prompts, 1937 rules, 2026-09-16) the
premise is backwards: injections on prompts whose top1/top2 ratio is below
1.05 were 56% carried and 5.6% hindsight, against 44% and 7.3% on prompts
with a clear winner (ratio >= 1.5). A near-tie means two rules apply, not
that neither does. At the old default of 1.5 the gate silenced 1773 prompts
and carried injections fell from 684 to 163. "Is this relevant at all?" is
(c)'s question, and the 2-slug cap already bounds the cost of a tie. The
knob stays for a user who wants it: any value above 1.0 re-enables (b).

BM25F's Laplace idf caps out at ln((N-0.5)/1.5+1), which grows with the
vault's doc count N — a term present in exactly one doc of a one-rule vault
is worth ~0.29, versus ~3.0 once the vault holds 30 rules. A fixed
absolute_floor calibrated for a mature vault is therefore unreachable in a
young one, so below floor_reference_docs the floor is scaled down by the
ratio of the vault's idf ceiling to the reference vault's idf ceiling
(clamped to never exceed the configured floor); at or above
floor_reference_docs the floor is unchanged.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

DEFAULT_THRESHOLDS: dict = {
    "term_overlap_min": 2,
    "relative_gap": 1.0,
    "absolute_floor": 2.0,
    "floor_reference_docs": 30,
}


@dataclass
class GateResult:
    accepted_slugs: list[str] = field(default_factory=list)
    silence_reason: str | None = None
    effective_floor: float | None = None


def _overlap(query: list[str], doc_tokens: set[str]) -> int:
    return len(set(query) & doc_tokens)


def idf_max(n: int) -> float:
    """The BM25F Laplace idf of a term present in exactly 1 of n docs."""
    return math.log((n - 0.5) / 1.5 + 1.0)


def effective_absolute_floor(
    floor: float, doc_count: int | None, reference_docs: int
) -> float:
    """Scale ``floor`` down for young vaults where it would be unreachable.

    Below ``reference_docs`` the idf ceiling is lower than at the reference
    point, so the floor is scaled by idf_max(doc_count) / idf_max(reference_docs),
    clamped at 1 so the result never exceeds the configured floor. With no
    usable doc_count or reference_docs the floor is returned unscaled.
    """
    if doc_count is None or doc_count <= 0 or reference_docs <= 1:
        return floor
    ratio = idf_max(doc_count) / idf_max(reference_docs)
    return floor * min(1.0, ratio)


def evaluate_gates(
    scores: list[tuple[str, float]],
    *,
    query_tokens: list[str],
    doc_tokens_by_slug: dict[str, set[str]],
    thresholds: dict,
    doc_count: int | None = None,
) -> GateResult:
    """Run the gates and return at most 2 accepted slugs (top-1, [top-2])."""
    if not scores:
        return GateResult(silence_reason="index_missing")

    top1_slug, top1_score = scores[0]
    top2 = scores[1] if len(scores) > 1 else (None, 0.0)

    t_overlap_min = int(thresholds.get("term_overlap_min", 2))
    rel_gap = float(thresholds.get("relative_gap", 1.0))
    configured_floor = float(thresholds.get("absolute_floor", 2.0))
    reference_docs = int(thresholds.get("floor_reference_docs", 30))
    abs_floor = effective_absolute_floor(configured_floor, doc_count, reference_docs)

    # (c) absolute floor — cheapest, check first.
    if top1_score < abs_floor:
        return GateResult(silence_reason="absolute_floor_fail", effective_floor=abs_floor)

    # (b) relative gap — off at <= 1.0; s2 == 0 is trivially passing.
    if rel_gap > 1.0 and top2[1] > 0 and top1_score < rel_gap * top2[1]:
        return GateResult(silence_reason="relative_gap_fail", effective_floor=abs_floor)

    # (a) term overlap.
    if _overlap(query_tokens, doc_tokens_by_slug.get(top1_slug, set())) < t_overlap_min:
        return GateResult(silence_reason="term_overlap_fail", effective_floor=abs_floor)

    accepted = [top1_slug]
    if top2[0] is not None:
        top2_slug, top2_score = top2
        if (
            top2_score >= abs_floor
            and _overlap(query_tokens, doc_tokens_by_slug.get(top2_slug, set())) >= t_overlap_min
        ):
            accepted.append(top2_slug)

    return GateResult(accepted_slugs=accepted, effective_floor=abs_floor)
