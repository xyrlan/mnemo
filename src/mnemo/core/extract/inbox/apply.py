"""Top-level ``apply_pages`` dispatch + drift/stem-collision guardrails.

Table-driven OCP fix (PR I): the previous monolithic ``apply_pages``
inlined three sequential ``if/elif`` branches inside the per-page loop.
Now each branch is a tuple ``(predicate, handler)`` registered in
``_DISPATCH``; adding a new apply branch is a new row, not an edit
to ``apply_pages``.

The three dispatch handlers live in :mod:`branches`:

- :func:`branches.upgrade._apply_upgrade_proposed`
- :func:`branches.auto_promoted._apply_auto_promoted`
- :func:`branches.inbox_flow._apply_inbox`
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from mnemo.core.backfill.origin import (
    is_backfill_entry,
    is_backfill_markdown,
    is_backfill_page,
)
from mnemo.core.extract.demotion import (
    is_demoted_entry,
    is_demoted_markdown,
    is_demoted_page,
)
from mnemo.core.extract import reference_gate
from mnemo.core.extract.inbox.branches.auto_promoted import _apply_auto_promoted
from mnemo.core.extract.inbox.branches.inbox_flow import _apply_inbox
from mnemo.core.extract.inbox.branches.universal_promotion import (
    _apply_universal_promotion,
    _universal_threshold,
    merged_projects_for,
)
from mnemo.core.extract.inbox.branches.upgrade import _apply_upgrade_proposed
from mnemo.core.extract.inbox.dedup import (
    SimilarityIndex,
    _detect_drift_slug,
    _detect_similar_existing,
    _detect_slug_identity,
    _detect_stem_collision,
)
from mnemo.core.extract.inbox.io import atomic_write
from mnemo.core.extract.inbox.paths import (
    _is_auto_promoted_target,
    _promoted_path,
    _sibling_path,
    _target_path_for_page,
)
from mnemo.core.extract.inbox.rendering import _render_page, _same_but_for_run_stamps
from mnemo.core.extract.inbox.types import ApplyResult, ExtractedPage
from mnemo.core.extract.scanner import SACRED_STATUSES, ExtractionState, StateEntry
from mnemo.core.rule_activation.index import is_universal


# ---------------------------------------------------------------------------
# Table-driven dispatch (OCP fix). Each row is (predicate, handler). Predicates
# are evaluated top-to-bottom; the first match wins. The fallback row pinned
# to the bottom (``lambda *_: True``) routes everything that didn't match.
# ---------------------------------------------------------------------------


def _is_universal_promotion(
    page: ExtractedPage,
    entry: StateEntry | None,
    target: Path,
    is_auto: bool,
) -> bool:
    """Page accumulates >= universalThreshold distinct projects across sources.

    Only fires for fresh inbox-staging targets:
    - ``is_auto`` false (target lives under ``shared/_inbox/<type>/``)
    - prior entry is either absent or already at status="inbox" — auto_promoted
      entries gaining a second project keep going through the upgrade branch
      (which writes a ``.update-proposed.md`` sibling for human review rather
      than overwriting the sacred file).

    Backfill-origin pages never qualify. Universal promotion writes into the
    sacred dir unreviewed, which is exactly what the origin gate exists to
    prevent; returning False here drops the page through to the inbox
    fallback row instead.
    """
    if is_auto:
        return False
    if is_backfill_page(page):
        return False
    if page.unverified_feedback:
        return False
    # #417: a page the reference gate did not clear stages for review; two
    # projects' worth of sources does not make a generic aphorism a rule.
    if not reference_gate.cleared(page):
        return False
    if entry is not None and entry.status == "auto_promoted":
        return False
    projects = merged_projects_for(page, entry)
    return is_universal(projects, _universal_threshold())


def _is_upgrade(
    page: ExtractedPage,
    entry: StateEntry | None,
    target: Path,
    is_auto: bool,
) -> bool:
    """Multi-source re-emission of an already-auto_promoted slug."""
    return (
        not is_auto
        and entry is not None
        and entry.status == "auto_promoted"
    )


def _is_auto_branch(
    page: ExtractedPage,
    entry: StateEntry | None,
    target: Path,
    is_auto: bool,
) -> bool:
    return is_auto


_Predicate = Callable[[ExtractedPage, "StateEntry | None", Path, bool], bool]
_Handler = Callable[..., None]


def _run_universal_promotion(
    page: ExtractedPage,
    entry: StateEntry | None,
    target: Path,
    vault_root: Path,
    state: ExtractionState,
    run_id: str,
    force: bool,
    result: ApplyResult,
) -> None:
    _apply_universal_promotion(
        page, entry, target, vault_root, state, run_id, force, result,
    )


def _run_upgrade(
    page: ExtractedPage,
    entry: StateEntry | None,
    target: Path,
    vault_root: Path,
    state: ExtractionState,
    run_id: str,
    force: bool,
    result: ApplyResult,
) -> None:
    # ``_is_upgrade`` guarantees entry is not None.
    assert entry is not None
    _apply_upgrade_proposed(page, entry, vault_root, run_id, result)


def _run_auto(
    page: ExtractedPage,
    entry: StateEntry | None,
    target: Path,
    vault_root: Path,
    state: ExtractionState,
    run_id: str,
    force: bool,
    result: ApplyResult,
) -> None:
    _apply_auto_promoted(
        page, entry, target, vault_root, state, run_id, force, result,
    )


def _run_inbox(
    page: ExtractedPage,
    entry: StateEntry | None,
    target: Path,
    vault_root: Path,
    state: ExtractionState,
    run_id: str,
    force: bool,
    result: ApplyResult,
) -> None:
    _apply_inbox(
        page, entry, target, vault_root, state, run_id, force, result,
    )


_DISPATCH: list[tuple[_Predicate, _Handler]] = [
    # Multi-source-multi-project pages bypass the inbox flow and land
    # directly in shared/<type>/. Must precede the upgrade + auto rows
    # because both of those assume single-project lineage.
    (_is_universal_promotion, _run_universal_promotion),
    (_is_upgrade, _run_upgrade),
    (_is_auto_branch, _run_auto),
    (lambda *_: True, _run_inbox),  # fallback: always-match must stay last
]


def _resolve_sticky_origin(
    page: ExtractedPage,
    entry: StateEntry | None,
    vault_root: Path,
) -> None:
    """Restore a backfill origin the current chunk no longer shows us.

    ``page.origin_backfill`` is derived by ``_parse_pages_from_response`` from
    the sources present in *this* chunk. A harvested memory file is dirty
    exactly once, so from the second extract onwards a page re-emitted under
    the same slug from some live source arrives with the flag already gone —
    and then walks straight through every gate that reads it.

    Two durable sources of truth, consulted in cost order:

    1. ``StateEntry.origin_backfill`` — free, and set by every branch below;
    2. the already-staged ``shared/_inbox/<type>/<slug>.md`` — one small read,
       only when the entry has no answer. This is what heals vaults whose
       state file predates the field, and it also catches a page staged by a
       code path that forgot to stamp the entry.

    Mutates the page in place so *every* downstream gate — ``_target_path_for_
    page``, ``_is_universal_promotion``, ``rendering._render_page`` — sees the
    same answer. Never clears the flag: origin is sticky by design, because
    deriving it fresh each run is the bug.
    """
    if is_backfill_page(page):
        return
    if is_backfill_entry(entry):
        page.origin_backfill = True
        return
    staged = vault_root / "shared" / "_inbox" / page.type / f"{page.slug}.md"
    if is_backfill_markdown(staged):
        page.origin_backfill = True


def _resolve_sticky_demotion(
    page: ExtractedPage,
    entry: StateEntry | None,
    vault_root: Path,
) -> None:
    """Restore a demotion the current run's gate could not tell us about.

    ``page.unverified_feedback`` is set by ``evidence.verify_page``, which
    returns early for any page that is not ``type: feedback``. A page demoted
    on run N is staged as a ``reference`` page, so on run N+1 the LLM sees it
    among the existing *reference* rules and re-emits it as one — the gate
    never runs, the flag arrives False, and the page goes straight through the
    single-source auto-promote door into ``shared/`` (#177).

    Two durable sources of truth, consulted in cost order, mirroring
    :func:`_resolve_sticky_origin`:

    1. ``StateEntry.unverified_feedback`` — free, and set by
       :func:`_stamp_entry_demotion` below;
    2. the already-staged ``shared/_inbox/<type>/<slug>.md``, which still
       carries ``demoted_from: feedback`` — one small read, only when the
       entry has no answer. This heals vaults whose state file predates the
       field.

    Mutates the page in place so every downstream gate sees the same answer.
    Never clears the flag: a page the gate refused once stays staged until a
    person reviews it, because re-deriving the answer each run is the bug.
    """
    if is_demoted_page(page):
        return
    if is_demoted_entry(entry):
        page.unverified_feedback = True
        return
    staged = vault_root / "shared" / "_inbox" / page.type / f"{page.slug}.md"
    if is_demoted_markdown(staged):
        page.unverified_feedback = True


def _stamp_entry_demotion(
    state: ExtractionState, key: str, page: ExtractedPage,
) -> None:
    """Persist a demotion onto whatever entry now lives at *key*.

    Runs after dispatch, like :func:`_stamp_entry_origin`, so it covers the
    branches that mutate an existing entry and the ones that install a fresh
    one. Only ever sets True.

    Unlike the origin stamp, :data:`SACRED_STATUSES` entries are **not**
    skipped: that skip exists to keep a live auto-promoted page refreshable,
    and a demoted page has no business being at a sacred status in the first
    place. Stamping one that already is (a page leaked by this bug before the
    fix) is what lets a later run see the demotion and route it back to the
    inbox rather than keep overwriting the sacred copy.
    """
    if not is_demoted_page(page):
        return
    entry = state.entries.get(key)
    if entry is None:
        return
    entry.unverified_feedback = True


def _stamp_entry_origin(
    state: ExtractionState, key: str, page: ExtractedPage,
) -> None:
    """Persist a backfill origin onto whatever entry now lives at *key*.

    Runs after dispatch so it covers both the branches that mutate an existing
    entry and the ones that install a fresh ``StateEntry``; a single write here
    means no branch has to remember to carry the flag. Only ever sets True, so
    a later run that no longer sees the origin cannot unstick it.

    Entries at a :data:`SACRED_STATUSES` status are skipped. Stamping those
    froze **live** pages (Task 9b review): one backfill
    reconstruction co-citing an auto-promoted page routes through the upgrade
    branch — which deliberately leaves the sacred file and the entry alone —
    and a stamp applied there made every later purely-live update take the
    ``_inbox``/``.proposed.md`` road forever, so the sacred page was never
    refreshed again. Skipping cannot open a leak: with no staged page under the
    key, the gate has nothing to keep staged.
    """
    if not is_backfill_page(page):
        return
    entry = state.entries.get(key)
    if entry is None or entry.status in SACRED_STATUSES:
        return
    entry.origin_backfill = True


def _stage_cross_type_proposal(
    page: ExtractedPage,
    existing_type: str,
    vault_root: Path,
    run_id: str,
    result: ApplyResult,
) -> None:
    """Stage a ``.proposed.md`` beside the page this slug already exists as.

    The proposal is keyed to the EXISTING page's type, not the arriving one, so
    it lands next to the page a reviewer has to compare it against rather than
    opening a second home for the slug. Nothing else is touched: no state entry
    is created or mutated, and the existing page is not read or rewritten — the
    merge is a human call (#187).
    """
    twin = replace(page, type=existing_type)
    content = _render_page(page, run_id=run_id, auto_promoted=False)
    sibling = _sibling_path(_promoted_path(vault_root, twin), vault_root)
    # Nothing here writes a state entry, so every later run re-detects the same
    # collision and lands back on this exact path. ``_render_page`` stamps
    # ``extracted_at``/``extraction_run`` from the run id, so an unconditional
    # write would churn an unreviewed proposal on every extract run with only
    # those two lines moving. Compare what the page actually says instead.
    if sibling.exists() and _same_but_for_run_stamps(sibling.read_text(encoding="utf-8"), content):
        return
    atomic_write(sibling, content)
    result.sibling_proposed.append((f"{existing_type}/{page.slug}", str(sibling)))


def apply_pages(
    pages: list[ExtractedPage],
    state: ExtractionState,
    vault_root: Path,
    *,
    run_id: str | None = None,
    force: bool = False,
) -> ApplyResult:
    run_id = run_id or datetime.now().isoformat(timespec="seconds")
    result = ApplyResult()

    # Built lazily, one per page type, from the state as it stands at loop
    # start, then kept current: every handled page is fed back in via
    # ``SimilarityIndex.add`` so that two mutually-similar NEW pages in one
    # batch (different slugs, so ``dedupe_by_slug`` does not merge them) end up
    # as one page rather than two. Without that, the index is a snapshot and
    # same-batch duplicates both get written.
    sim_indexes: dict[str, SimilarityIndex] = {}

    def _sim_index(page_type: str) -> SimilarityIndex:
        if page_type not in sim_indexes:
            sim_indexes[page_type] = SimilarityIndex(state, vault_root, page_type)
        return sim_indexes[page_type]

    for page in pages:
        # Zeroth layer: this exact slug already lives under a DIFFERENT type.
        # The three layers below all filter state by `f"{page.type}/"`, so none
        # of them can see it, and their shared body gate would reject it anyway
        # (#187: the real pairs score below the p90 of unrelated noise).
        #
        # Staged, not redirected. A redirect would reach `_apply_auto_promoted`
        # -> `_handle_target_exists`, which OVERWRITES the sacred file: in every
        # real pair the existing page is the `verified` auto-promoted one and the
        # arrival is `inferred`, so redirecting lets the weaker page destroy the
        # stronger. Deciding which of two same-slug pages is canonical is also
        # not a call this pipeline can make unsupervised. Note `page.type` is
        # deliberately NOT rewritten: the state key, `_resolve_sticky_demotion`'s
        # probe and the similarity index all read it.
        cross_type = _detect_slug_identity(page, state, vault_root)
        if cross_type is not None:
            _stage_cross_type_proposal(page, cross_type, vault_root, run_id, result)
            continue

        # Anti-drift guardrail: if the LLM chose a new slug for what is clearly
        # a rewrite of an existing page (same sources + similar body), redirect
        # the slug so the existing page gets updated in place instead of a
        # duplicate being created under the drifted slug.
        drift_target = _detect_drift_slug(page, state, vault_root)
        if drift_target is not None:
            page.slug = drift_target
        else:
            stem_target = _detect_stem_collision(page, state, vault_root)
            if stem_target is not None:
                page.slug = stem_target
            else:
                # Third layer: neither the sources nor the slug line up, but the
                # page says what an existing page of this type already says.
                # Redirecting here is what makes source_count accrue.
                similar = _detect_similar_existing(page, _sim_index(page.type))
                if similar is not None:
                    page.slug = similar

        key = f"{page.type}/{page.slug}"
        entry = state.entries.get(key)
        # Before anything reads the origin flag: put back the one this chunk
        # could not tell us about. Must precede _target_path_for_page — that
        # is the single-source auto-promote door, and it is the first gate a
        # laundered page reaches.
        _resolve_sticky_origin(page, entry, vault_root)
        # Same treatment for the evidence gate's verdict, and for the same
        # reason: it is derived per run, and the run that re-emits a demoted
        # slug as a native reference page derives nothing at all (#177).
        _resolve_sticky_demotion(page, entry, vault_root)
        target = _target_path_for_page(page, vault_root)
        is_auto = _is_auto_promoted_target(target, vault_root)

        # Row 1 (special-case fast path): source_hash unchanged → skip.
        # Stays inline because it's a "do nothing + continue" — registering it
        # as a no-op handler in the table would obscure the loop control flow.
        if entry is not None and entry.source_hash == page.source_hash and not force:
            _stamp_entry_origin(state, key, page)
            _stamp_entry_demotion(state, key, page)
            result.unchanged_skipped.append(key)
            continue

        # Demotions are judged for their stamp (#432) but stage on the
        # evidence gate's word; `staged unverified` already counts them.
        if not page.unverified_feedback and reference_gate.held(page, vault_root):
            result.reference_held.append(key)
        dismissed_before = len(result.dismissed_skipped)
        for predicate, handler in _DISPATCH:
            if predicate(page, entry, target, is_auto):
                handler(
                    page, entry, target, vault_root,
                    state, run_id, force, result,
                )
                break
        _stamp_entry_origin(state, key, page)
        _stamp_entry_demotion(state, key, page)
        # Feed the page back into the index so a later page in this same batch
        # that says the same thing redirects onto it — but only if this page
        # actually landed. A page dropped as dismissed_skipped wrote nothing, so
        # registering it would let a later similar page redirect onto a slug
        # that is not there, and be dropped in turn. Upgrade-proposed pages
        # (which write only a ``.proposed.md`` sibling) are still indexed, which
        # is benign: the slug itself does exist on disk.
        if len(result.dismissed_skipped) == dismissed_before:
            _sim_index(page.type).add(page)

    return result
