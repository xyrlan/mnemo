"""Cross-chunk dedup + slug-drift / stem-collision guardrails.

Pulled out of the pre-v0.9 ``inbox.py`` monolith. ``_detect_stem_collision``
and ``_detect_drift_slug`` previously inlined the
``vault_root / "shared" / type / f"{slug}.md"`` shape four times; in PR I
all four sites route through ``paths._promoted_path`` /
``paths._inbox_path`` (D1 cross-file consolidation target).
"""
from __future__ import annotations

from dataclasses import replace
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
        # Everything not listed here is taken from ``chosen`` verbatim, via
        # dataclasses.replace — so a field added to ExtractedPage later is
        # carried through this merge without anyone having to remember it.
        # Field-by-field rebuilds are how origin_backfill got dropped at this
        # exact site (Task 6b), and a dropped flag opens a bypass silently
        # rather than raising.
        merged.append(replace(
            chosen,
            source_files=all_sources,
            tags=all_tags,
            # Sticky across the merge: if any contributing page was
            # reconstructed from an archived transcript, the merged page is
            # partly reconstructed too and must stay behind the origin gate.
            origin_backfill=any(p.origin_backfill for p in items),
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


# ---------------------------------------------------------------------------
# Third-layer guardrail: content similarity against EVERY existing page of the
# same type, no source-set requirement. Stem collision and drift both require
# the slugs or sources to line up; a rule re-learned in another project under
# fresh wording matches neither, which is how families of 5-10 near-duplicates
# accumulated and why source_count never grew past 1.
# ---------------------------------------------------------------------------

# Calibrated 2026-09-02 against the real dogfood vault: all 1436 feedback pages,
# all 1,030,330 unordered pairs (not a sample — an earlier 200-page sample put
# the ceiling at 0.377, which is wrong and understated the top of the range).
#
# The original 0.5 threshold never fired: the whole-vault maximum weighted score
# is 0.4978. Known duplicate families sit at 0.32-0.36, so weighted score alone
# has no cutoff that separates them from coincidence — the top of the range is
# a mix of both. Ranked by weighted score, the top pairs are:
#
#   1. 0.4978  toast-z-index-above-overlays / toast-z-index-layering   (TRUE)
#   2. 0.4452  css-test-file-isolation / test-artifacts-must-not-...   (false)
#   3. 0.4367  webview-multiple-windows-... / webview-session-...      (false)
#   4. 0.4227  managed-workflow-native-... / version-management-...    (false)
#   8. 0.3767  tri-layered-ui-state-... / default-filter-state-...     (false)
#
# Hence the second, independent signal below. The name gate is what removes
# those false pairs — all four above are name-gate rejects.
SIMILARITY_THRESHOLD = 0.32

# Second signal: Jaccard over the SET of stop-word-stripped stemmed NAME tokens.
#
# The honest tradeoff: this gate earns its place by rejecting the false pairs
# listed above, but it also rejects real duplicates whose two names are
# asymmetric — the highest-scoring TRUE pair in the vault (toast-z-index, 0.4978
# at name overlap 0.25) is rejected, as are pdf-generation-chrome-headless
# (0.167) and e2e-auth-ratelimit-isolation (0.125). Long or lopsided names
# dilute set Jaccard, and the stemmer splits some shared concepts
# (isolation/isolat, rate/rate-limit). No axis-aligned cutoff on these two
# signals admits every true family while excluding every false one, so we take
# the conservative side: missing a merge leaves two pages, a wrong merge
# destroys one.
#
# At 0.32/0.27, 38 pairs vault-wide would redirect. Two of those 38 are judged
# false on inspection:
#   dialog-zindex-above-drawer / toast-z-index-above-overlays
#   migration-before-code-deploy / tight-deployment-window-schema-rename
NAME_OVERLAP_MIN = 0.27

# Stripped before the name-overlap comparison: these carry no topical signal and
# their presence/absence is pure LLM phrasing noise across re-learned rules.
_NAME_STOP_WORDS = frozenset({
    "a", "an", "the", "of", "for", "in", "on", "to", "and", "or", "with",
    "vs", "via", "over", "not", "no", "never", "always", "use", "prefer",
    "when", "before", "after", "from", "by", "at", "is", "are", "be",
})

_NAME_WEIGHT, _DESC_WEIGHT, _BODY_WEIGHT = 3, 2, 1

# Measured 2026-09-02 end-to-end on the 1436-page vault (file IO + frontmatter
# parse included): index build 0.50s, ``find`` 0.15s per page. ``find`` is a full
# linear scan of every profile, so a run costs O(pages_in_run x vault_size) —
# quadratic in run size. Acceptable for a background extract job at this scale;
# no inverted index yet, revisit if the vault or the per-run page count grows by
# an order of magnitude.


def _weighted_profile(name: str, description: str, body: str) -> dict[str, int]:
    """Stem-folded token counts, weighted by which field the token came from.

    ``tokenize`` splits on ``[^a-z0-9_-]+``, so Markdown emphasis and the
    ``**Why:**`` / ``**How to apply:**`` scaffolding contribute the bare words
    ``why`` / ``how`` / ``to`` / ``apply`` rather than punctuation noise. Those
    scaffolding words are shared by every extracted page, which nudges every
    pair's score upward by a constant, which is precisely why the measured
    ceiling on real pages is 0.4978 rather than anything near 1.0. See the
    calibration note on :data:`SIMILARITY_THRESHOLD`.
    """
    from mnemo.core.reflex.tokenizer import tokenize

    profile: dict[str, int] = {}
    for text, weight in ((name, _NAME_WEIGHT), (description, _DESC_WEIGHT), (body, _BODY_WEIGHT)):
        for tok in tokenize(text or ""):
            stem = _stem_word(tok)
            profile[stem] = profile.get(stem, 0) + weight
    return profile


def weighted_jaccard(a: dict[str, int], b: dict[str, int]) -> float:
    """Multiset Jaccard: sum of per-stem minima over sum of per-stem maxima."""
    if not a or not b:
        return 0.0
    keys = set(a) | set(b)
    inter = sum(min(a.get(k, 0), b.get(k, 0)) for k in keys)
    union = sum(max(a.get(k, 0), b.get(k, 0)) for k in keys)
    return inter / union if union else 0.0


def _name_tokens(name: str) -> set[str]:
    """Stop-word-stripped stemmed token SET for a page name.

    A set, not a multiset: names are short enough that a repeated word says
    nothing, and set Jaccard is what the 2026-09-02 calibration measured.
    """
    from mnemo.core.reflex.tokenizer import tokenize

    out: set[str] = set()
    for tok in tokenize(name or ""):
        if tok in _NAME_STOP_WORDS:
            continue
        stem = _stem_word(tok)
        if stem in _NAME_STOP_WORDS:
            continue
        out.add(stem)
    return out


def name_overlap(a: set[str], b: set[str]) -> float:
    """Plain set Jaccard over two name-token sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class SimilarityIndex:
    """Profiles of every on-disk page of one type, built once per apply run."""

    def __init__(self, state: ExtractionState, vault_root: Path, page_type: str) -> None:
        from mnemo.core.filters import parse_frontmatter
        from mnemo.core.text_utils import strip_graph_section

        self._profiles: dict[str, dict[str, int]] = {}
        self._names: dict[str, set[str]] = {}
        for key, entry in list(state.entries.items()):
            if not key.startswith(f"{page_type}/"):
                continue
            # Never redirect onto a dismissed slug. Both apply branches bail
            # early on status="dismissed" (``dismissed_skipped``, nothing
            # written), so a redirect there silently DESTROYS the new page
            # instead of reinforcing anything.
            if entry.status == "dismissed":
                continue
            slug = key.split("/", 1)[1]
            target = _existing_target(vault_root, page_type, slug)
            if target is None:
                continue
            try:
                text = target.read_text(encoding="utf-8")
            except OSError:
                continue
            fm = parse_frontmatter(text)
            name = str(fm.get("name") or slug)
            # Strip the appended "## Sources" wikilink block before profiling:
            # its file-path tokens are shared by every page written from the
            # same briefing tree and would inflate every pairwise score.
            self._profiles[slug] = _weighted_profile(
                name,
                str(fm.get("description") or ""),
                _extract_body(strip_graph_section(text)),
            )
            self._names[slug] = _name_tokens(name)

    def add(self, page: ExtractedPage) -> None:
        """Register a page written earlier in this same ``apply_pages`` run.

        The index is otherwise a snapshot of the state at loop start, so two
        mutually-similar NEW pages in one batch (different slugs, so
        ``dedupe_by_slug`` does not merge them) would both be written. Adding
        each page as it is handled makes the second one redirect onto the
        first — exactly the intended outcome.
        """
        if not page.slug:
            return
        self._profiles[page.slug] = _weighted_profile(
            page.name, page.description, page.body,
        )
        self._names[page.slug] = _name_tokens(page.name)

    def find(self, page: ExtractedPage, threshold: float | None = None) -> str | None:
        """Return the most similar existing slug passing BOTH gates, or None.

        Two independent signals must agree (see the calibration note on
        :data:`SIMILARITY_THRESHOLD`): the weighted body/description/name
        Jaccard at or above the threshold, AND the name-token overlap at or
        above :data:`NAME_OVERLAP_MIN`. Weighted score alone cannot separate
        real duplicates from coincidence on the measured vault.

        Both knobs are read from module scope at call time (not bound as
        default arguments) so they stay patchable in one place.
        """
        cutoff = SIMILARITY_THRESHOLD if threshold is None else threshold
        name_cutoff = NAME_OVERLAP_MIN
        probe = _weighted_profile(page.name, page.description, page.body)
        probe_name = _name_tokens(page.name)
        best_slug, best = None, 0.0
        for slug, profile in self._profiles.items():
            if slug == page.slug:
                continue
            score = weighted_jaccard(probe, profile)
            if score < cutoff:
                continue
            if name_overlap(probe_name, self._names.get(slug, set())) < name_cutoff:
                continue
            # Deterministic tie-break: on an equal score prefer the
            # lexicographically smaller slug, so the result does not depend on
            # state-file insertion order. No ``best_slug is not None`` guard is
            # needed: score >= cutoff > 0.0 == the initial ``best``, so the first
            # candidate to reach here always takes the ``score > best`` branch.
            if score > best or (score == best and slug < best_slug):
                best_slug, best = slug, score
        return best_slug


def _detect_similar_existing(page: ExtractedPage, index: SimilarityIndex) -> str | None:
    return index.find(page)
