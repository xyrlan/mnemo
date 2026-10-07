"""Surface cross-project near-duplicate rules as universal-promotion candidates.

Universal promotion (``branches/universal_promotion``) fires only when the
*same* slug accrues source files from ``scoping.universalThreshold`` distinct
projects. In practice that almost never happens: rule names are LLM-generated
per extraction, so two projects that learn the same lesson end up with two
different slugs and neither ever crosses the threshold. The v0.15.1 dogfood
measured 541 of 556 indexed rules sitting at exactly one project with only 2
universal rules in the vault.

This module closes the diagnostic half of that gap: it clusters rules that
*look* like the same lesson learned in different projects, so the doctor can
report real promotion candidates instead of "every single-project rule is one
project away". It never mutates the vault — promotion stays a human/proposal
decision because a false merge would corrupt two rules at once.

Similarity is a weighted Jaccard over three fields already carried by the
rule-activation index (no file reads, no LLM): name, topic tags, body preview.
Clustering is transitive (union-find), so three projects converging on one
lesson collapse into a single candidate rather than three pairs.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from mnemo.core.extract.inbox.dedup import _stem_word

# Field weights. Name dominates — it is the most compressed statement of the
# rule — but body carries enough signal to rescue differently-phrased titles.
_W_NAME = 0.5
_W_TAGS = 0.2
_W_BODY = 0.3

DEFAULT_THRESHOLD = 0.55

_STOPWORDS = frozenset({
    "a", "an", "the", "to", "of", "in", "on", "for", "and", "or", "is", "are",
    "be", "not", "no", "never", "always", "must", "should", "use", "using",
    "it", "its", "this", "that", "with", "from", "at", "by", "as", "if",
})


@lru_cache(maxsize=65536)
def _token(raw: str) -> str:
    """One lowercased word as a token, or ``""`` when it is dropped.

    Cached because a vault's rules share their vocabulary: 3,345 rules spent
    half of the doctor row tokenizing the same words over again (#571).
    """
    word = "".join(ch for ch in raw if ch.isalnum() or ch == "-").strip("-")
    if not word or word in _STOPWORDS:
        return ""
    return _stem_word(word)


def _tokens(text: str) -> set[str]:
    """Lowercase word tokens, stopwords dropped, inflections collapsed."""
    out = {_token(raw) for raw in str(text or "").lower().split()}
    out.discard("")
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class UniversalCandidate:
    """A cluster of same-lesson rules spanning distinct projects."""

    slugs: list[str]
    projects: list[str]
    similarity: float
    type: str


class _Fields:
    __slots__ = ("slug", "type", "name", "tags", "body", "projects")

    def __init__(self, slug: str, rule: dict) -> None:
        self.slug = slug
        self.type = str(rule.get("type") or "")
        self.name = _tokens(rule.get("name") or slug)
        self.tags = _tokens(" ".join(rule.get("topic_tags") or []))
        self.body = _tokens(rule.get("body_preview") or "")
        self.projects = tuple(sorted({p for p in (rule.get("projects") or []) if p}))


def _similarity(a: _Fields, b: _Fields) -> float:
    return (
        _W_NAME * _jaccard(a.name, b.name)
        + _W_TAGS * _jaccard(a.tags, b.tags)
        + _W_BODY * _jaccard(a.body, b.body)
    )


def _candidate_pairs(items: list[_Fields], threshold: float) -> list[tuple[int, int]]:
    """Every same-type pair ``(i, j)``, ``i < j``, that could reach *threshold*.

    Disjoint token sets score 0, so a pair sharing no name token tops out at
    ``_W_TAGS + _W_BODY`` = 0.5. Above that — the default 0.55 is — only pairs
    sharing a name token are returned: 70k of the 2.27M same-type pairs the
    all-pairs loop scored on a 3,345-rule index (#571). The pairs left out are
    exactly ones that loop scored and threw away, so the clusters are the same.
    At a lower threshold every same-type pair is returned, as before.
    """
    # The margin covers the bound and the score summing in different orders.
    if threshold <= _W_TAGS + _W_BODY + 1e-9:
        return [
            (i, j)
            for i in range(len(items))
            for j in range(i + 1, len(items))
            if items[i].type == items[j].type
        ]
    postings: dict[tuple[str, str], list[int]] = {}
    for i, item in enumerate(items):
        for token in item.name:
            postings.setdefault((item.type, token), []).append(i)
    pairs: set[tuple[int, int]] = set()
    for i, item in enumerate(items):
        for token in item.name:
            pairs.update((i, j) for j in postings[(item.type, token)] if j > i)
    return sorted(pairs)


def find_universal_candidates(
    index: dict,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_projects: int = 2,
) -> list[UniversalCandidate]:
    """Return clusters of similar rules that span ``min_projects`` projects.

    Rules already marked ``universal`` are skipped — they are the outcome this
    detector is trying to produce. Rules of different ``type`` are never
    clustered: a ``reference`` page and a ``feedback`` rule saying the same
    thing are not the same artifact.

    ``index`` is a loaded ``rule-activation-index.json`` (or any dict with the
    same ``{"rules": {slug: {...}}}`` shape). Results are sorted by project
    count desc, then similarity desc, then slug — stable across runs.
    """
    rules = (index or {}).get("rules") or {}
    items = [
        _Fields(slug, rule)
        for slug, rule in sorted(rules.items())
        if not rule.get("universal") and (rule.get("projects") or [])
    ]
    if len(items) < 2:
        return []

    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # Best pairwise score per cluster, reported as the candidate's similarity.
    # Pairs are visited in (i, j) order, as the all-pairs loop did: a merge
    # keeps the lower root's best score, so the order is part of the output.
    best: dict[int, float] = {}
    for i, j in _candidate_pairs(items, threshold):
        a, b = items[i], items[j]
        if set(a.projects) == set(b.projects):
            # Same project(s) on both sides — merging them is dedup work,
            # not promotion. dedup_rules owns that case.
            continue
        score = _similarity(a, b)
        if score < threshold:
            continue
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)
        root = find(i)
        best[root] = max(best.get(root, 0.0), score)

    clusters: dict[int, list[int]] = {}
    for i in range(len(items)):
        clusters.setdefault(find(i), []).append(i)

    out: list[UniversalCandidate] = []
    for root, members in clusters.items():
        if len(members) < 2:
            continue
        projects = sorted({p for m in members for p in items[m].projects})
        if len(projects) < min_projects:
            continue
        out.append(UniversalCandidate(
            slugs=sorted(items[m].slug for m in members),
            projects=projects,
            similarity=round(best.get(root, 0.0), 4),
            type=items[members[0]].type,
        ))

    out.sort(key=lambda c: (-len(c.projects), -c.similarity, tuple(c.slugs)))
    return out
