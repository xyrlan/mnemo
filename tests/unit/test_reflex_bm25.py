from __future__ import annotations

from mnemo.core.reflex.bm25 import score_docs, DEFAULT_WEIGHTS, DEFAULT_PARAMS


def _index_with(slugs_fields_lengths, avg_lengths, postings):
    """Helper: build a minimal index dict shape for tests."""
    return {
        "avg_field_length": avg_lengths,
        "doc_count": len(slugs_fields_lengths),
        "docs": {
            slug: {"field_length": lengths, "preview": "", "stability": "stable",
                   "projects": [], "universal": False}
            for slug, lengths in slugs_fields_lengths.items()
        },
        "postings": postings,
    }


def test_score_empty_query_returns_empty():
    idx = _index_with({"a": {"name": 1, "topic_tags": 0, "aliases": 0, "description": 0, "body": 5}},
                      {"name": 1.0, "topic_tags": 0.0, "aliases": 0.0, "description": 0.0, "body": 5.0},
                      {})
    assert score_docs(idx, query_tokens=[], candidate_slugs=["a"]) == []


def test_score_candidates_filter_is_respected():
    idx = _index_with(
        {
            "in_scope": {"name": 1, "topic_tags": 0, "aliases": 0, "description": 0, "body": 1},
            "out_of_scope": {"name": 1, "topic_tags": 0, "aliases": 0, "description": 0, "body": 1},
        },
        {"name": 1.0, "topic_tags": 0.0, "aliases": 0.0, "description": 0.0, "body": 1.0},
        {"foo": [
            {"slug": "in_scope", "tf": {"name": 1, "topic_tags": 0, "aliases": 0, "description": 0, "body": 0}},
            {"slug": "out_of_scope", "tf": {"name": 1, "topic_tags": 0, "aliases": 0, "description": 0, "body": 0}},
        ]},
    )
    results = score_docs(idx, query_tokens=["foo"], candidate_slugs=["in_scope"])
    assert len(results) == 1
    assert results[0][0] == "in_scope"


def test_score_is_descending_and_weighted_by_field():
    # Two rules: A matches in name (weight 3.0), B matches in body (weight 1.0).
    # Same field_length. Expect A > B.
    idx = _index_with(
        {
            "A": {"name": 1, "topic_tags": 0, "aliases": 0, "description": 0, "body": 10},
            "B": {"name": 10, "topic_tags": 0, "aliases": 0, "description": 0, "body": 10},
        },
        {"name": 5.5, "topic_tags": 0.0, "aliases": 0.0, "description": 0.0, "body": 10.0},
        {"prisma": [
            {"slug": "A", "tf": {"name": 1, "topic_tags": 0, "aliases": 0, "description": 0, "body": 0}},
            {"slug": "B", "tf": {"name": 0, "topic_tags": 0, "aliases": 0, "description": 0, "body": 1}},
        ]},
    )
    results = score_docs(idx, query_tokens=["prisma"], candidate_slugs=["A", "B"])
    slugs = [slug for slug, score in results]
    assert slugs == ["A", "B"]
    assert results[0][1] > results[1][1]
