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
            "Always run migrations before deploy", ["repo-b"],
            body="Run pending migrations before deploying or the app 500s.",
            tags=["deployment"],
        ),
        "Run migrations before deploying": _rule(
            "Run migrations before deploying", ["repo-a"],
            body="Deploying before running migrations makes the app 500.",
            tags=["deployment"],
        ),
    })
    cands = find_universal_candidates(idx)
    assert len(cands) == 1
    assert cands[0].projects == ["repo-a", "repo-b"]
    assert len(cands[0].slugs) == 2


def test_ignores_near_duplicates_inside_one_project():
    idx = _index({
        "Always run migrations before deploy": _rule(
            "Always run migrations before deploy", ["repo-b"],
            body="Run pending migrations before deploying or the app 500s.",
        ),
        "Run migrations before deploying": _rule(
            "Run migrations before deploying", ["repo-b"],
            body="Deploying before running migrations makes the app 500.",
        ),
    })
    assert find_universal_candidates(idx) == []


def test_ignores_unrelated_rules():
    idx = _index({
        "Always run migrations before deploy": _rule(
            "Always run migrations before deploy", ["repo-b"],
            body="Run pending migrations before deploying.",
        ),
        "Use hex color tokens in the design system": _rule(
            "Use hex color tokens in the design system", ["repo-a"],
            body="Never hardcode rgb values inside components.",
        ),
    })
    assert find_universal_candidates(idx) == []


def test_excludes_rules_already_universal():
    idx = _index({
        "Always run migrations before deploy": _rule(
            "Always run migrations before deploy", ["repo-b"], universal=True,
            body="Run pending migrations before deploying or the app 500s.",
        ),
        "Run migrations before deploying": _rule(
            "Run migrations before deploying", ["repo-a"],
            body="Deploying before running migrations makes the app 500.",
        ),
    })
    assert find_universal_candidates(idx) == []


def test_clusters_three_projects_into_one_candidate():
    idx = _index({
        "Always run migrations before deploy": _rule(
            "Always run migrations before deploy", ["repo-b"],
            body="Run pending migrations before deploying or the app 500s.",
        ),
        "Run migrations before deploying": _rule(
            "Run migrations before deploying", ["repo-a"],
            body="Deploying before running migrations makes the app 500s.",
        ),
        "Run migrations before deploy": _rule(
            "Run migrations before deploy", ["repo-d"],
            body="Deploying before running migrations makes the app 500s.",
        ),
    })
    cands = find_universal_candidates(idx)
    assert len(cands) == 1
    assert cands[0].projects == ["repo-a", "repo-b", "repo-d"]
    assert len(cands[0].slugs) == 3


def test_does_not_cross_rule_types():
    idx = _index({
        "Always run migrations before deploy": _rule(
            "Always run migrations before deploy", ["repo-b"],
            body="Run pending migrations before deploying or the app 500s.",
            type_="feedback",
        ),
        "Run migrations before deploying": _rule(
            "Run migrations before deploying", ["repo-a"],
            body="Deploying before running migrations makes the app 500.",
            type_="reference",
        ),
    })
    assert find_universal_candidates(idx) == []


def test_results_are_deterministic_and_ranked_by_project_count():
    idx = _index({
        "Run migrations before deploy": _rule(
            "Run migrations before deploy", ["repo-b"],
            body="Deploying before running migrations makes the app 500s.",
        ),
        "Run migrations before deploying": _rule(
            "Run migrations before deploying", ["repo-a"],
            body="Deploying before running migrations makes the app 500s.",
        ),
        "Never hardcode api base urls": _rule(
            "Never hardcode api base urls", ["repo-c"],
            body="Read the api base url from env config instead of literals.",
        ),
        "Never hardcode api base url": _rule(
            "Never hardcode api base url", ["bingx-robot"],
            body="Read the api base url from env config instead of literals.",
        ),
        "Do not hardcode api base urls": _rule(
            "Do not hardcode api base urls", ["repo-d"],
            body="Read the api base url from env config instead of literals.",
        ),
    })
    first = find_universal_candidates(idx)
    assert [len(c.projects) for c in first] == [3, 2]
    assert find_universal_candidates(idx) == first


def test_empty_or_missing_index_returns_empty():
    assert find_universal_candidates({}) == []
    assert find_universal_candidates({"rules": {}}) == []
