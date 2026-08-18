"""Tests for the optional ``query`` param on ``list_rules_by_topic``.

Workstream 3 (query-aware ranking): with ``query`` present, rules whose BM25F
score against the query passes a gate rise to the top; everything else keeps
the source_count order. With ``query`` absent, behavior is byte-identical to
the pre-existing sort.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.mcp.tools import _query_rerank, list_rules_by_topic
from mnemo.core.reflex import index as reflex_index


def _write_page(
    vault: Path,
    slug: str,
    *,
    tags: list[str],
    sources: list[str],
    description: str = "generic placeholder description",
    body: str = "the rule body\n",
) -> Path:
    target_dir = vault / "shared" / "feedback"
    target_dir.mkdir(parents=True, exist_ok=True)
    sources_yaml = "\n".join(f"  - {s}" for s in sources)
    tags_yaml = "\n".join(f"  - {t}" for t in tags)
    text = (
        "---\n"
        f"name: {slug}\n"
        f"description: {description}\n"
        "type: feedback\n"
        "stability: stable\n"
        "sources:\n"
        f"{sources_yaml}\n"
        "tags:\n"
        f"{tags_yaml}\n"
        "---\n\n"
        f"{body}"
    )
    target = target_dir / f"{slug}.md"
    target.write_text(text)
    return target


def _build_reflex_index(vault: Path) -> None:
    reflex_index.write_index(vault, reflex_index.build_index(vault))


def _fixture_vault(vault: Path) -> None:
    """Three rules in topic ``testing``: two multi-source but irrelevant, one
    single-source whose text matches the webhook query."""
    _write_page(
        vault, "aaa-popular-rule",
        tags=["auto-promoted", "testing"],
        sources=["bots/a/m.md", "bots/b/m.md", "bots/c/m.md"],
        description="prefer tabs over spaces in makefiles",
        body="Makefiles require tab indentation.\n",
    )
    _write_page(
        vault, "bbb-other-rule",
        tags=["auto-promoted", "testing"],
        sources=["bots/a/n.md", "bots/b/n.md"],
        description="pin dependency versions in lockfiles",
        body="Always commit the lockfile.\n",
    )
    _write_page(
        vault, "zzz-webhook-idempotency",
        tags=["auto-promoted", "testing"],
        sources=["bots/a/w.md"],
        description="webhook handlers must be idempotent with deduplication",
        body="Webhook retries duplicate deliveries; guard with idempotency keys.\n",
    )


# --- query=None regression guard ---


def test_query_none_keeps_source_count_order(tmp_vault):
    _fixture_vault(tmp_vault)
    _build_reflex_index(tmp_vault)

    result = list_rules_by_topic(tmp_vault, "testing")
    assert [r["slug"] for r in result] == [
        "aaa-popular-rule", "bbb-other-rule", "zzz-webhook-idempotency",
    ]


# --- query with a strong match ---


def test_query_match_lifts_relevant_rule_above_higher_source_count(tmp_vault):
    _fixture_vault(tmp_vault)
    _build_reflex_index(tmp_vault)

    result = list_rules_by_topic(
        tmp_vault, "testing",
        query="add idempotency deduplication to the webhook handler",
    )
    slugs = [r["slug"] for r in result]
    assert slugs[0] == "zzz-webhook-idempotency"
    # Non-hits keep their source_count order below the hit.
    assert slugs[1:] == ["aaa-popular-rule", "bbb-other-rule"]


def test_query_no_overlap_falls_back_to_source_count_order(tmp_vault):
    _fixture_vault(tmp_vault)
    _build_reflex_index(tmp_vault)

    result = list_rules_by_topic(
        tmp_vault, "testing",
        query="quaternion slerp interpolation kernel",
    )
    assert [r["slug"] for r in result] == [
        "aaa-popular-rule", "bbb-other-rule", "zzz-webhook-idempotency",
    ]


def test_query_with_missing_reflex_index_is_noop(tmp_vault):
    _fixture_vault(tmp_vault)
    # No reflex index written.

    result = list_rules_by_topic(
        tmp_vault, "testing",
        query="add idempotency deduplication to the webhook handler",
    )
    assert [r["slug"] for r in result] == [
        "aaa-popular-rule", "bbb-other-rule", "zzz-webhook-idempotency",
    ]


# --- named mutation: the gate is load-bearing ---


def test_gate_blocks_sub_threshold_hits_from_rising():
    """Named mutation guard: with min_score=1.0 a weak hit (score 0.4) stays in
    source_count order; setting min_score=0 lets it jump. If someone deletes
    the gate (equivalent to min_score=0), the first assertion fails."""
    matches = [
        {"slug": "popular", "type": "feedback", "source_count": 3},
        {"slug": "weak-hit", "type": "feedback", "source_count": 1},
    ]
    scored = [("weak-hit", 0.4)]

    gated = _query_rerank(matches, scored, min_score=1.0)
    assert [m["slug"] for m in gated] == ["popular", "weak-hit"]

    ungated = _query_rerank(matches, scored, min_score=0.0)
    assert [m["slug"] for m in ungated] == ["weak-hit", "popular"]


def test_rerank_orders_hits_by_score_desc():
    matches = [
        {"slug": "a", "type": "feedback", "source_count": 5},
        {"slug": "b", "type": "feedback", "source_count": 4},
        {"slug": "c", "type": "feedback", "source_count": 3},
    ]
    scored = [("c", 2.5), ("b", 1.2)]

    out = _query_rerank(matches, scored, min_score=1.0)
    assert [m["slug"] for m in out] == ["c", "b", "a"]
