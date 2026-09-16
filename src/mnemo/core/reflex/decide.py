"""One reflex decision, with no side effects.

The ``UserPromptSubmit`` hook and ``mnemo replay`` both need the same answer to
the same question — "given this index, this project and this prompt, what
would reflex inject?" — and a replay that re-implements the ranking and the
gates measures its own copy, not the hook. So the pure part of the decision
lives here and the hook is the only place that logs, caches and emits.

Nothing in this module reads config files, touches the session cache, or
writes a log line. Callers pass the resolved reflex config and the per-project
threshold overrides in; they get back what was accepted, why nothing was, and
the receipt the decision was made on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Union

from mnemo.core.reflex import bm25, gates
from mnemo.core.reflex.tokenizer import tokenize_query


@dataclass
class Decision:
    """What one prompt would get, and the numbers behind it.

    ``scores`` is empty and ``thresholds`` is ``{}`` when the decision was
    made before ranking ran (``below_min_tokens``, ``index_missing``): the
    hook's log distinguishes "retrieval looked and found nothing" from
    "retrieval never ran" by the absence of those keys, and so must this.
    """

    accepted: list[str] = field(default_factory=list)
    silence_reason: str | None = None
    scores: list[tuple[str, float]] = field(default_factory=list)
    query_tokens: list[str] = field(default_factory=list)
    thresholds: dict = field(default_factory=dict)
    # The index the ranking ran against (None when it never ran or was missing),
    # so a caller that passed a loader does not have to load it twice.
    index: dict | None = None


def candidates_for_project(index: dict, project: str) -> list[str]:
    """Slugs in scope for ``project``: its own rules plus the universal ones.

    Retired rules (``doc["retired"]``, see ``reflex/index.py``) are never
    candidates. This is the single chokepoint for that: the hook, ``replay``
    and ``mnemo why`` all rank through here, so none of them filters again.
    """
    docs = index.get("docs") or {}
    return [
        slug for slug, doc in docs.items()
        if (project in (doc.get("projects") or []) or doc.get("universal"))
        and not doc.get("retired")
    ]


def doc_token_sets(index: dict, slugs: list[str] | None = None) -> dict[str, set[str]]:
    """Per-doc token UNION across every indexed field, for the overlap gate.

    One pass over the postings. With ``slugs`` given only those docs are
    collected (what the hook needs for its top-2); with ``None`` every doc is,
    which is what a replay of thousands of prompts wants to compute once.
    """
    target = None if slugs is None else set(slugs)
    out: dict[str, set[str]] = {s: set() for s in (slugs or [])}
    for term, entries in (index.get("postings") or {}).items():
        for entry in entries:
            slug = entry["slug"]
            if target is not None and slug not in target:
                continue
            out.setdefault(slug, set()).add(term)
    return out


def gate_thresholds(reflex_cfg: dict, overrides: dict | None = None) -> dict:
    """The four gate thresholds, per-project calibration winning over config, per key."""
    thresholds = reflex_cfg.get("thresholds") or {}
    overrides = overrides or {}
    return {
        "term_overlap_min": int(overrides.get(
            "term_overlap_min", thresholds.get("termOverlapMin", 2))),
        "relative_gap": float(overrides.get(
            "relative_gap", thresholds.get("relativeGap", 1.0))),
        "absolute_floor": float(overrides.get(
            "absolute_floor", thresholds.get("absoluteFloor", 2.0))),
        "floor_reference_docs": int(overrides.get(
            "floor_reference_docs", thresholds.get("floorReferenceDocs", 30))),
    }


def decide(
    index: Union[dict, None, Callable[[], "dict | None"]],
    *,
    project: str,
    prompt: str,
    reflex_cfg: dict,
    overrides: dict | None = None,
    doc_tokens: dict[str, set[str]] | None = None,
) -> Decision:
    """Rank the prompt against the project's candidates and run the triple-gate.

    Mirrors the hook's order exactly: the token pre-gate runs before the index
    is even consulted, so a two-word prompt against a missing index is
    ``below_min_tokens``, not ``index_missing``. ``doc_tokens`` is an optional
    precomputed :func:`doc_token_sets` over the whole index; without it the
    top-2's token sets are rebuilt from the postings on every call.

    ``index`` may be the loaded index, ``None``, or a zero-argument loader
    that is only called once the pre-gate has passed — the hook uses that so a
    prompt rejected for length never pays for reading the index from disk.
    """
    thresholds_cfg = reflex_cfg.get("thresholds") or {}
    min_tokens = int(thresholds_cfg.get("minQueryTokens", 3))
    q_tokens = tokenize_query(prompt)
    if len(set(q_tokens)) < min_tokens:
        return Decision(silence_reason="below_min_tokens", query_tokens=q_tokens)

    if callable(index):
        index = index()
    if index is None:
        return Decision(silence_reason="index_missing", query_tokens=q_tokens)

    candidates = candidates_for_project(index, project)
    if not candidates:
        return Decision(silence_reason="index_missing", query_tokens=q_tokens, index=index)

    bm25f_cfg = reflex_cfg.get("bm25f") or {}
    weights = bm25f_cfg.get("fieldWeights") or bm25.DEFAULT_WEIGHTS
    params = bm25f_cfg or bm25.DEFAULT_PARAMS
    scores = bm25.score_docs(index, query_tokens=q_tokens,
                             candidate_slugs=candidates,
                             weights=weights, params=params)

    thresholds = gate_thresholds(reflex_cfg, overrides)
    top = [slug for slug, _ in scores[:2]]
    if doc_tokens is None:
        doc_tokens = doc_token_sets(index, top)
    doc_count = int(index.get("doc_count", 0))
    result = gates.evaluate_gates(
        scores,
        query_tokens=q_tokens,
        doc_tokens_by_slug=doc_tokens,
        thresholds=thresholds,
        doc_count=doc_count,
    )
    # Record what was actually used so the receipt carries both the configured
    # floor and the effective one — `mnemo why` explains a scaled decision from
    # the pair. `absolute_floor` stays the configured value.
    if result.effective_floor is not None:
        thresholds["absolute_floor_effective"] = result.effective_floor
    thresholds["doc_count"] = doc_count
    return Decision(
        accepted=list(result.accepted_slugs),
        silence_reason=None if result.accepted_slugs else (result.silence_reason or "index_missing"),
        scores=scores,
        query_tokens=q_tokens,
        thresholds=thresholds,
        index=index,
    )
