"""Which live rules a correction could be contradicting.

The contradiction pass (``friction/link.py``) cannot read the whole vault, so
it reads the rules ranked closest to the correction. This module does that
ranking, and does it over the **whole** eligible pool for the project.

The existing ``existing_rules`` hint cannot stand in for it. It shows at most
80 rules ordered by ``source_count``, and since ~98% of rules have
``source_count = 1`` that cut lands inside a tie settled by slug order —
measured 2026-09-16 at 37.9% of mnemo's eligible pool. A contradiction asked
against that sample would leave the ledger's silence meaningless.

No ranking code lives here. The index is the reflex index, the pool is
``decide.candidates_for_project`` (so whatever that excludes — a retired rule,
once retirement lands — is excluded here too), and the scorer is
``reflex.bm25.score_docs``. This module only turns the top of that ranking
back into pages.

BM25F is lexical and contradiction is not, which is why the cut is generous
(:data:`CONTRADICTION_CANDIDATES`) and the judgement is left to the model. A
contradiction ranked below the cut is missed — the ledger may under-report,
and ``mnemo friction`` prints the rank distribution of confirmed links so the
cut can be revisited against evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mnemo.core.filters import derive_rule_slug, is_consumer_visible
from mnemo.core.reclassify_types import split_frontmatter
from mnemo.core.reflex import bm25
from mnemo.core.reflex import index as reflex_index
from mnemo.core.reflex.decide import candidates_for_project
from mnemo.core.reflex.tokenizer import tokenize_query
from mnemo.core.text_utils import retrieval_body

#: How many ranked rules, bodies included, the contradiction pass is shown.
CONTRADICTION_CANDIDATES = 40

# The page types the reflex index walks (``reflex/index.build_index``).
_PAGE_TYPES = ("feedback", "user", "reference", "project")


@dataclass(frozen=True)
class Candidate:
    """One ranked rule, with what the contradiction pass needs to judge it."""

    slug: str
    name: str
    #: The rule text as retrieval sees it — frontmatter, graph section and
    #: advisory notes removed (``text_utils.retrieval_body``).
    body: str
    #: The BM25F score against the correction text.
    score: float
    #: 1-based position in the returned list.
    rank: int


def correction_text(correction: Any) -> tuple[str, str]:
    """``(quote, rule)`` from a correction, whichever shape it arrives in.

    Accepts ``core.corrections.Correction`` (``quote``, ``rule``) and
    ``friction.ledger.FrictionRecord`` (``quote``, ``rule_text``) — the live
    path holds the first, the backfill and the ledger the second.
    """
    quote = getattr(correction, "quote", "") or ""
    rule = getattr(correction, "rule", None)
    if rule is None:
        rule = getattr(correction, "rule_text", "")
    return str(quote), str(rule or "")


def rank(
    vault_root: Path,
    correction: Any,
    *,
    project: str,
    index: dict | None = None,
    limit: int | None = None,
) -> list[Candidate]:
    """The top rules in ``project``'s eligible pool, ranked against ``correction``.

    Scores the correction's quote and rule text together against every slug
    ``candidates_for_project`` admits and returns up to ``limit`` (default
    :data:`CONTRADICTION_CANDIDATES`) as :class:`Candidate`, best first.
    Only rules that share at least one term with the correction score; a
    pool with no lexical overlap returns fewer, possibly none.

    ``index`` lets a caller that ranks many corrections — the backfill —
    load the reflex index once. Without it the on-disk index is read, and
    built in memory (never written) when it is missing or unreadable.

    A slug the index still holds but whose page is gone is skipped and the
    next one takes its place, so a stale index costs a position, not a slot.

    Never raises on vault content: an unreadable page is skipped.
    """
    cap = CONTRADICTION_CANDIDATES if limit is None else int(limit)
    if cap <= 0:
        return []
    root = Path(vault_root)
    quote, rule = correction_text(correction)
    tokens = tokenize_query(quote + "\n" + rule)
    if not tokens:
        return []

    idx = index if index is not None else _load_or_build(root)
    pool = candidates_for_project(idx, project)
    if not pool:
        return []
    scored = bm25.score_docs(idx, query_tokens=tokens, candidate_slugs=pool)

    pages = _PageFinder(root)
    out: list[Candidate] = []
    for slug, score in scored:
        page = pages.get(slug)
        if page is None:
            continue
        name, body = page
        out.append(Candidate(slug=slug, name=name, body=body, score=score, rank=len(out) + 1))
        if len(out) >= cap:
            break
    return out


def _load_or_build(vault_root: Path) -> dict:
    idx = reflex_index.load_index(vault_root)
    if idx is None:
        idx = reflex_index.build_index(vault_root)
    return idx


class _PageFinder:
    """Slug → ``(name, body)`` for live pages, reading as little as it can.

    The common case is a page whose file stem is its slug, so that path is
    tried first and confirmed against the derived slug. Only a miss pays for
    one walk of ``shared/``, the same walk and the same visibility gate the
    index was built with, after which every lookup is a dict hit.
    """

    def __init__(self, vault_root: Path) -> None:
        self._root = vault_root
        self._by_slug: dict[str, Path] | None = None

    def get(self, slug: str) -> tuple[str, str] | None:
        if "/" not in slug and "\\" not in slug:
            for page_type in _PAGE_TYPES:
                found = self._read(self._root / "shared" / page_type / (slug + ".md"), slug)
                if found is not None:
                    return found
        path = self._walk().get(slug)
        return None if path is None else self._read(path, slug)

    def _read(self, path: Path, slug: str) -> tuple[str, str] | None:
        if not path.is_file():
            return None
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        fm, body = split_frontmatter(text)
        if not is_consumer_visible(path, fm, self._root):
            return None
        if derive_rule_slug(fm, path.stem) != slug:
            return None
        name = fm.get("name")
        return (name if isinstance(name, str) and name.strip() else slug), retrieval_body(body).strip()

    def _walk(self) -> dict[str, Path]:
        if self._by_slug is not None:
            return self._by_slug
        found: dict[str, Path] = {}
        for page_type in _PAGE_TYPES:
            type_dir = self._root / "shared" / page_type
            if not type_dir.is_dir():
                continue
            for md_path in sorted(type_dir.glob("*.md")):
                try:
                    text = md_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                fm, _body = split_frontmatter(text)
                if not is_consumer_visible(md_path, fm, self._root):
                    continue
                found[derive_rule_slug(fm, md_path.stem)] = md_path
        self._by_slug = found
        return found


__all__ = ["CONTRADICTION_CANDIDATES", "Candidate", "correction_text", "rank"]
