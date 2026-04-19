"""Triple-gate confidence check for Reflex.

Silence is the default; emission requires ALL THREE to pass against the
top-1 candidate:

  (a) term-overlap >= term_overlap_min across the UNION of indexed fields
  (b) relative gap  s[0] >= relative_gap * s[1]   (or s[1] == 0)
  (c) absolute floor s[0] >= absolute_floor

If top-1 passes, top-2 is included ONLY IF it ALSO passes (a) and its score
clears the absolute_floor. We deliberately do not re-check relative gap on
top-2 — the purpose of top-2 is "nearly as good as top-1, not worth hiding."
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_THRESHOLDS: dict = {
    "term_overlap_min": 2,
    "relative_gap": 1.5,
    "absolute_floor": 2.0,
}


@dataclass
class GateResult:
    accepted_slugs: list[str] = field(default_factory=list)
    silence_reason: str | None = None


def _overlap(query: list[str], doc_tokens: set[str]) -> int:
    return len(set(query) & doc_tokens)


def evaluate_gates(
    scores: list[tuple[str, float]],
    *,
    query_tokens: list[str],
    doc_tokens_by_slug: dict[str, set[str]],
    thresholds: dict,
) -> GateResult:
    """Run the triple-gate and return at most 2 accepted slugs (top-1, [top-2])."""
    if not scores:
        return GateResult(silence_reason="index_missing")

    top1_slug, top1_score = scores[0]
    top2 = scores[1] if len(scores) > 1 else (None, 0.0)

    t_overlap_min = int(thresholds.get("term_overlap_min", 2))
    rel_gap = float(thresholds.get("relative_gap", 1.5))
    abs_floor = float(thresholds.get("absolute_floor", 2.0))

    # (c) absolute floor — cheapest, check first.
    if top1_score < abs_floor:
        return GateResult(silence_reason="absolute_floor_fail")

    # (b) relative gap — s2 == 0 is trivially passing.
    if top2[1] > 0 and top1_score < rel_gap * top2[1]:
        return GateResult(silence_reason="relative_gap_fail")

    # (a) term overlap.
    if _overlap(query_tokens, doc_tokens_by_slug.get(top1_slug, set())) < t_overlap_min:
        return GateResult(silence_reason="term_overlap_fail")

    accepted = [top1_slug]
    if top2[0] is not None:
        top2_slug, top2_score = top2
        if (
            top2_score >= abs_floor
            and _overlap(query_tokens, doc_tokens_by_slug.get(top2_slug, set())) >= t_overlap_min
        ):
            accepted.append(top2_slug)

    return GateResult(accepted_slugs=accepted)
