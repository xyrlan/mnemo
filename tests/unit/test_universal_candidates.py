# tests/unit/test_universal_candidates.py
"""Cross-project near-duplicate detection for universal promotion.

Exact-slug promotion never fires in practice: LLM-generated names differ per
project, so two projects learning the same lesson produce two distinct slugs
and `universalThreshold` is never crossed. These tests lock the near-duplicate
detector that surfaces those pairs as promotion candidates.
"""
from __future__ import annotations

from mnemo.core.universal_candidates import find_universal_candidates


def _rule(name, projects, *, body="", tags=None, universal=False, type_="feedback"):
    return {
        "type": type_,
        "name": name,
        "topic_tags": tags or [],
        "body_preview": body,
        "projects": list(projects),
        "universal": universal,
    }


def _index(rules: dict) -> dict:
    return {"rules": rules}


def test_finds_near_duplicate_across_two_projects():
    idx = _index({
        "Always run migrations before deploy": _rule(
            "Always run migrations before deploy", ["meunu"],
            body="Run pending migrations before deploying or the app 500s.",
            tags=["deployment"],
        ),
        "Run migrations before deploying": _rule(
            "Run migrations before deploying", ["clubinho"],
            body="Deploying before running migrations makes the app 500.",
            tags=["deployment"],
        ),
    })
    cands = find_universal_candidates(idx)
    assert len(cands) == 1
    assert cands[0].projects == ["clubinho", "meunu"]
    assert len(cands[0].slugs) == 2


def test_ignores_near_duplicates_inside_one_project():
    idx = _index({
        "Always run migrations before deploy": _rule(
            "Always run migrations before deploy", ["meunu"],
            body="Run pending migrations before deploying or the app 500s.",
        ),
        "Run migrations before deploying": _rule(
            "Run migrations before deploying", ["meunu"],
            body="Deploying before running migrations makes the app 500.",
        ),
    })
    assert find_universal_candidates(idx) == []


def test_ignores_unrelated_rules():
    idx = _index({
        "Always run migrations before deploy": _rule(
            "Always run migrations before deploy", ["meunu"],
            body="Run pending migrations before deploying.",
        ),
        "Use hex color tokens in the design system": _rule(
            "Use hex color tokens in the design system", ["clubinho"],
            body="Never hardcode rgb values inside components.",
        ),
    })
    assert find_universal_candidates(idx) == []


def test_excludes_rules_already_universal():
    idx = _index({
        "Always run migrations before deploy": _rule(
            "Always run migrations before deploy", ["meunu"], universal=True,
            body="Run pending migrations before deploying or the app 500s.",
        ),
        "Run migrations before deploying": _rule(
            "Run migrations before deploying", ["clubinho"],
            body="Deploying before running migrations makes the app 500.",
        ),
    })
    assert find_universal_candidates(idx) == []


def test_clusters_three_projects_into_one_candidate():
    idx = _index({
        "Always run migrations before deploy": _rule(
            "Always run migrations before deploy", ["meunu"],
            body="Run pending migrations before deploying or the app 500s.",
        ),
        "Run migrations before deploying": _rule(
            "Run migrations before deploying", ["clubinho"],
            body="Deploying before running migrations makes the app 500s.",
        ),
        "Run migrations before deploy": _rule(
            "Run migrations before deploy", ["sg-imports"],
            body="Deploying before running migrations makes the app 500s.",
        ),
    })
    cands = find_universal_candidates(idx)
    assert len(cands) == 1
    assert cands[0].projects == ["clubinho", "meunu", "sg-imports"]
    assert len(cands[0].slugs) == 3


def test_does_not_cross_rule_types():
    idx = _index({
        "Always run migrations before deploy": _rule(
            "Always run migrations before deploy", ["meunu"],
            body="Run pending migrations before deploying or the app 500s.",
            type_="feedback",
        ),
        "Run migrations before deploying": _rule(
            "Run migrations before deploying", ["clubinho"],
            body="Deploying before running migrations makes the app 500.",
            type_="reference",
        ),
    })
    assert find_universal_candidates(idx) == []


def test_results_are_deterministic_and_ranked_by_project_count():
    idx = _index({
        "Run migrations before deploy": _rule(
            "Run migrations before deploy", ["meunu"],
            body="Deploying before running migrations makes the app 500s.",
        ),
        "Run migrations before deploying": _rule(
            "Run migrations before deploying", ["clubinho"],
            body="Deploying before running migrations makes the app 500s.",
        ),
        "Never hardcode api base urls": _rule(
            "Never hardcode api base urls", ["clearframe"],
            body="Read the api base url from env config instead of literals.",
        ),
        "Never hardcode api base url": _rule(
            "Never hardcode api base url", ["bingx-robot"],
            body="Read the api base url from env config instead of literals.",
        ),
        "Do not hardcode api base urls": _rule(
            "Do not hardcode api base urls", ["sg-imports"],
            body="Read the api base url from env config instead of literals.",
        ),
    })
    first = find_universal_candidates(idx)
    assert [len(c.projects) for c in first] == [3, 2]
    assert find_universal_candidates(idx) == first


def test_empty_or_missing_index_returns_empty():
    assert find_universal_candidates({}) == []
    assert find_universal_candidates({"rules": {}}) == []
