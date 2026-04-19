"""BM25F scoring over the Reflex index.

BM25F reference: Robertson et al. — field-weighted variant of BM25.
We adopt the simplified "pseudo-term-frequency" formulation:

    ~tf_t,d = sum_f  weight_f * tf_t,d,f / (1 - b + b * L_f,d / avgL_f)
    score(q, d) = sum_{t in q}  ~tf_t,d / (k1 + ~tf_t,d)  *  idf(t)
    idf(t) = log( (N - df_t + 0.5) / (df_t + 0.5) + 1 )

N = doc_count, df_t = number of docs containing term t (any field).

Design decisions:
- Per-field b is global (see spec BM25F parameters — "no per-field length
  normalization, premature").
- ``avgL_f`` is the vault-wide average field length. Small vaults → the
  denominator stays bounded so scores remain stable at day-1.
- No query-term weighting (all query tokens equal). Tried earlier during
  design; the triple-gate is a stronger safety rail than query boosting.
"""
from __future__ import annotations

import math
from typing import Iterable

DEFAULT_WEIGHTS: dict[str, float] = {
    "name": 3.0,
    "topic_tags": 3.0,
    "aliases": 2.5,
    "description": 2.0,
    "body": 1.0,
}

DEFAULT_PARAMS = {"k1": 1.5, "b": 0.75}


def score_docs(
    index: dict,
    *,
    query_tokens: list[str],
    candidate_slugs: Iterable[str],
    weights: dict[str, float] | None = None,
    params: dict | None = None,
) -> list[tuple[str, float]]:
    """Score candidate docs against the query. Returns [(slug, score), ...] desc."""
    if not query_tokens:
        return []
    w = weights or DEFAULT_WEIGHTS
    p = params or DEFAULT_PARAMS
    k1 = float(p.get("k1", 1.5))
    b = float(p.get("b", 0.75))

    docs = index.get("docs", {})
    postings = index.get("postings", {})
    avg = index.get("avg_field_length", {})
    N = int(index.get("doc_count", 0))

    candidate_set = {s for s in candidate_slugs if s in docs}
    if not candidate_set:
        return []

    # Precompute IDF per unique query term.
    unique_query = list(dict.fromkeys(query_tokens))
    idf: dict[str, float] = {}
    for term in unique_query:
        df = len(postings.get(term, []))
        # +1 Laplace on the idf formula keeps values non-negative.
        idf[term] = math.log((N - df + 0.5) / (df + 0.5) + 1.0)

    scores: dict[str, float] = {slug: 0.0 for slug in candidate_set}

    for term in unique_query:
        term_postings = postings.get(term, [])
        if not term_postings:
            continue
        for entry in term_postings:
            slug = entry["slug"]
            if slug not in candidate_set:
                continue
            doc = docs[slug]
            lengths = doc.get("field_length", {})

            weighted_tf = 0.0
            for field, weight in w.items():
                tf_f = int(entry["tf"].get(field, 0))
                if tf_f == 0:
                    continue
                L_f = int(lengths.get(field, 0))
                avg_L_f = float(avg.get(field, 0.0)) or 1.0
                denom = (1.0 - b) + b * (L_f / avg_L_f)
                if denom <= 0:
                    continue
                weighted_tf += weight * tf_f / denom
            if weighted_tf <= 0:
                continue
            sat = weighted_tf / (k1 + weighted_tf)
            scores[slug] += sat * idf[term]

    out = [(slug, score) for slug, score in scores.items() if score > 0]
    out.sort(key=lambda kv: (-kv[1], kv[0]))
    return out
