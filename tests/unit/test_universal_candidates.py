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


# --- #571: pruning pairs must not change a single result --------------------


def _all_pairs_reference(index, *, threshold=0.55, min_projects=2):
    """The scan as it was before #571: every pair scored, in order."""
    from mnemo.core import universal_candidates as U

    rules = (index or {}).get("rules") or {}
    items = [U._Fields(slug, rule) for slug, rule in sorted(rules.items())
             if not rule.get("universal") and (rule.get("projects") or [])]
    if len(items) < 2:
        return []
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    best = {}
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            if a.type != b.type or set(a.projects) == set(b.projects):
                continue
            score = U._similarity(a, b)
            if score < threshold:
                continue
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[max(ri, rj)] = min(ri, rj)
            root = find(i)
            best[root] = max(best.get(root, 0.0), score)
    clusters = {}
    for i in range(len(items)):
        clusters.setdefault(find(i), []).append(i)
    out = []
    for root, members in clusters.items():
        projects = sorted({p for m in members for p in items[m].projects})
        if len(members) < 2 or len(projects) < min_projects:
            continue
        out.append(U.UniversalCandidate(
            slugs=sorted(items[m].slug for m in members), projects=projects,
            similarity=round(best.get(root, 0.0), 4), type=items[members[0]].type))
    out.sort(key=lambda c: (-len(c.projects), -c.similarity, tuple(c.slugs)))
    return out


def _random_index(seed: int, n: int = 160) -> dict:
    """A vault small enough to score every pair, crowded enough to cluster."""
    import random

    rng = random.Random(seed)
    words = [f"w{k}" for k in range(24)] + ["the", "never", "run", "running", "tests"]
    rules = {}
    for k in range(n):
        name = " ".join(rng.sample(words, rng.randint(1, 4)))
        rules[f"rule-{seed}-{k}"] = _rule(
            name if rng.random() > 0.05 else "",
            rng.sample(["repo-a", "repo-b", "repo-c", "repo-d"], rng.randint(1, 2)),
            body=" ".join(rng.choice(words) for _ in range(rng.randint(0, 10))),
            tags=rng.sample(["git", "testing", "ci", "deploy"], rng.randint(0, 2)),
            universal=rng.random() < 0.05,
            type_=rng.choice(["feedback", "feedback", "reference"]),
        )
    return _index(rules)


def test_pruned_pairs_find_exactly_what_every_pair_finds():
    """Clusters, their order and their reported similarity all match.

    The reported similarity depends on the order pairs are merged in, so this
    checks the whole candidate, across thresholds above and below the 0.5
    bound where pruning switches off.
    """
    seen_big_cluster = False
    for seed in range(12):
        idx = _random_index(seed)
        for threshold in (0.35, 0.5, 0.55, 0.6, 0.7):
            got = find_universal_candidates(idx, threshold=threshold)
            assert got == _all_pairs_reference(idx, threshold=threshold), (seed, threshold)
            seen_big_cluster |= any(len(c.slugs) > 2 for c in got)
    assert seen_big_cluster, "the fixture never exercised a multi-merge cluster"


def test_pairs_without_a_shared_name_token_are_never_scored(monkeypatch):
    from mnemo.core import universal_candidates as U

    scored = []
    real = U._similarity
    monkeypatch.setattr(U, "_similarity", lambda a, b: scored.append((a, b)) or real(a, b))
    idx = _index({
        "a": _rule("deploy migrations first", ["repo-a"], body="same body words"),
        "b": _rule("deploy after migrating", ["repo-b"], body="same body words"),
        "c": _rule("lint before commit", ["repo-c"], body="same body words"),
    })
    find_universal_candidates(idx)
    assert {(a.slug, b.slug) for a, b in scored} == {("a", "b")}


def test_pruned_pairs_keep_the_all_pairs_order():
    """The clustering keeps a merged root's best score and drops the absorbed
    one's, so the order pairs arrive in is part of the result."""
    from mnemo.core import universal_candidates as U

    for seed in range(4):
        rules = _random_index(seed)["rules"]
        items = [U._Fields(slug, rule) for slug, rule in sorted(rules.items())]
        every = [(i, j) for i in range(len(items)) for j in range(i + 1, len(items))]
        assert list(U._pairs(items, 0.5)) == every
        assert list(U._pairs(items, 0.55)) == [
            (i, j) for i, j in every if items[i].name & items[j].name]
