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

from datetime import datetime
from pathlib import Path
from typing import Callable

from mnemo.core.extract.inbox.branches.auto_promoted import _apply_auto_promoted
from mnemo.core.extract.inbox.branches.inbox_flow import _apply_inbox
from mnemo.core.extract.inbox.branches.upgrade import _apply_upgrade_proposed
from mnemo.core.extract.inbox.dedup import _detect_drift_slug, _detect_stem_collision
from mnemo.core.extract.inbox.paths import _is_auto_promoted_target, _target_path_for_page
from mnemo.core.extract.inbox.types import ApplyResult, ExtractedPage
from mnemo.core.extract.scanner import ExtractionState, StateEntry


# ---------------------------------------------------------------------------
# Table-driven dispatch (OCP fix). Each row is (predicate, handler). Predicates
# are evaluated top-to-bottom; the first match wins. The fallback row pinned
# to the bottom (``lambda *_: True``) routes everything that didn't match.
# ---------------------------------------------------------------------------


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
    (_is_upgrade, _run_upgrade),
    (_is_auto_branch, _run_auto),
    (lambda *_: True, _run_inbox),  # fallback: always-match must stay last
]


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

    for page in pages:
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

        key = f"{page.type}/{page.slug}"
        entry = state.entries.get(key)
        target = _target_path_for_page(page, vault_root)
        is_auto = _is_auto_promoted_target(target, vault_root)

        # Row 1 (special-case fast path): source_hash unchanged → skip.
        # Stays inline because it's a "do nothing + continue" — registering it
        # as a no-op handler in the table would obscure the loop control flow.
        if entry is not None and entry.source_hash == page.source_hash and not force:
            result.unchanged_skipped.append(key)
            continue

        for predicate, handler in _DISPATCH:
            if predicate(page, entry, target, is_auto):
                handler(
                    page, entry, target, vault_root,
                    state, run_id, force, result,
                )
                break

    return result
