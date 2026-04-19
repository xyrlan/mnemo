"""Cross-chunk dedup + slug-drift / stem-collision guardrails.

Pulled out of the pre-v0.9 ``inbox.py`` monolith. ``_detect_stem_collision``
and ``_detect_drift_slug`` previously inlined the
``vault_root / "shared" / type / f"{slug}.md"`` shape four times; in PR I
all four sites route through ``paths._promoted_path`` /
``paths._inbox_path`` (D1 cross-file consolidation target).
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.extract.inbox import paths
from mnemo.core.extract.inbox.rendering import _extract_body
from mnemo.core.extract.inbox.types import ExtractedPage
from mnemo.core.extract.scanner import ExtractionState


def dedupe_by_slug(pages: list[ExtractedPage]) -> list[ExtractedPage]:
    """Merge pages that share a slug (cross-chunk cluster collision)."""
    groups: dict[str, list[ExtractedPage]] = {}
    for p in pages:
        key = f"{p.type}/{p.slug}"
        groups.setdefault(key, []).append(p)

    merged: list[ExtractedPage] = []
    for key, items in groups.items():
        if len(items) == 1:
            merged.append(items[0])
            continue
        # Union source files; body from the page with most sources
        chosen = max(items, key=lambda p: len(p.source_files))
        all_sources: list[str] = []
        for p in items:
            for sf in p.source_files:
                if sf not in all_sources:
                    all_sources.append(sf)
        # Union tags from all merged pages so the LLM's topic vocabulary is
        # preserved. Preserve order: chosen page first, then any extras.
        all_tags: list[str] = []
        for p in [chosen] + [p for p in items if p is not chosen]:
            for t in getattr(p, "tags", None) or []:
                if t not in all_tags:
                    all_tags.append(t)
        merged.append(ExtractedPage(
            slug=chosen.slug,
            type=chosen.type,
            name=chosen.name,
            description=chosen.description,
            body=chosen.body,
            source_files=all_sources,
            source_hash=chosen.source_hash,
            stability=getattr(chosen, "stability", None) or "stable",
            tags=all_tags,
            enforce=getattr(chosen, "enforce", None),
            activates_on=getattr(chosen, "activates_on", None),
        ))
    return merged


def _bodies_similar(a: str, b: str, threshold: float = 0.6) -> bool:
    """Cheap Jaccard similarity on lowercase word tokens.

    Used to decide whether a freshly-extracted page is a drifted rewrite of an
    existing page (same underlying rule, new slug) vs. a legitimately distinct
    rule that happens to share a source file.
    """
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return False
    common = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(common) / len(union) >= threshold


_STEM_SUFFIXES = (
    "ations", "ation", "ings", "ing", "ied", "ies", "ers", "ed", "es", "er",
)


def _stem_word(word: str) -> str:
    """Collapse common English inflections to a shared stem.

    Deliberately simple (no Porter stemmer dependency) — just enough to
    fold the dogfood collision between ``populate`` and ``populating`` into
    one canonical form. False merges are caught by the body-similarity
    check in ``_detect_stem_collision``.
    """
    w = word.lower()
    if len(w) < 4:
        return w
    for suf in _STEM_SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 4:
        return w[:-1]
    if w.endswith("e") and len(w) > 4:
        return w[:-1]
    return w


def _stem_slug(slug: str) -> str:
    return "-".join(_stem_word(tok) for tok in slug.split("-") if tok)


def _existing_target(vault_root: Path, page_type: str, slug: str) -> Path | None:
    """Return the on-disk target for ``page_type/slug``, or None if neither
    the promoted nor _inbox/ variant exists.

    Centralizes the "look in shared/<type>/, fall back to shared/_inbox/<type>/"
    probe that previously appeared inline in ``_detect_stem_collision`` and
    ``_detect_drift_slug``. Both helpers in ``paths`` accept an
    ``ExtractedPage``-shaped object — we synthesize a stub since only ``.type``
    and ``.slug`` are read.
    """
    stub = ExtractedPage(
        slug=slug,
        type=page_type,
        name="",
        description="",
        body="",
        source_files=[],
        source_hash="",
    )
    promoted = paths._promoted_path(vault_root, stub)
    if promoted.exists():
        return promoted
    inbox = paths._inbox_path(vault_root, stub)
    if inbox.exists():
        return inbox
    return None


def _detect_stem_collision(
    page: ExtractedPage,
    state: ExtractionState,
    vault_root: Path,
) -> str | None:
    """Return an existing slug whose stem matches ``page.slug``, or None.

    Second-layer guardrail that catches inflection drift across runs:
    ``auto-populate-…`` and ``auto-populating-…`` from different source
    sets should collapse to one canonical page. Unlike
    ``_detect_drift_slug`` (which requires identical source files), this
    check relies entirely on slug-stem equality plus body similarity.

    Skips the exact-match case (handled by the normal update flow) and
    stale state entries whose target files no longer exist on disk.
    """
    if not page.slug:
        return None
    candidate_stem = _stem_slug(page.slug)
    if not candidate_stem:
        return None
    for key, entry in state.entries.items():
        if not key.startswith(f"{page.type}/"):
            continue
        existing_slug = key.split("/", 1)[1]
        if existing_slug == page.slug:
            return None  # exact match — update path will handle it
        if _stem_slug(existing_slug) != candidate_stem:
            continue
        existing_target = _existing_target(vault_root, page.type, existing_slug)
        if existing_target is None:
            continue
        try:
            existing_text = existing_target.read_text(encoding="utf-8")
        except OSError:
            continue
        existing_body = _extract_body(existing_text)
        if _bodies_similar(page.body, existing_body):
            return existing_slug
    return None


def _detect_drift_slug(
    page: ExtractedPage,
    state: ExtractionState,
    vault_root: Path,
) -> str | None:
    """Return an existing slug this page is a drifted rewrite of, or None.

    Guardrail against LLM non-determinism in slug choice. Triggers when an
    existing state entry for the same ``<type>`` has the EXACT same source
    file set AND a body similar to the new page. Redirects the new page's
    slug to the existing one so ``apply_pages`` treats it as an update rather
    than a fresh write, preventing drift pairs from accumulating.

    Skips stale state entries whose target files no longer exist on disk.
    Handles the legitimate one-source-many-rules case via the body-similarity
    check: distinct rules from the same source file have disjoint tokens and
    fall below the threshold.
    """
    if not page.source_files:
        return None
    source_set = set(page.source_files)
    for key, entry in state.entries.items():
        if not key.startswith(f"{page.type}/"):
            continue
        existing_slug = key.split("/", 1)[1]
        if existing_slug == page.slug:
            return None  # already matching — no drift
        if set(entry.source_files or []) != source_set:
            continue
        # Same source set. Verify existing target file exists (stale state
        # entries are skipped) and compare body content.
        existing_target = _existing_target(vault_root, page.type, existing_slug)
        if existing_target is None:
            continue
        try:
            existing_text = existing_target.read_text(encoding="utf-8")
        except OSError:
            continue
        existing_body = _extract_body(existing_text)
        if _bodies_similar(page.body, existing_body):
            return existing_slug
    return None
