"""Apply branch for pages whose target lives in shared/<type>/ (the sacred dir).

Split from the 77-line ``_apply_auto_promoted`` in the pre-v0.9
``inbox.py`` monolith into per-status helpers (PR I):

- :func:`_handle_no_entry`              — first-time fresh write
- :func:`_handle_dismissed`             — user previously dismissed; skip
- :func:`_handle_inbox_to_auto_migrate` — v0.2→v0.3 status migration
- :func:`_handle_target_missing`        — sacred file deleted by user
- :func:`_handle_target_exists`         — user may or may not have edited it

The public entry point :func:`_apply_auto_promoted` keeps its original
name + signature so :mod:`apply` can register it as a status handler.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.extract.inbox.io import atomic_write, content_hash
from mnemo.core.extract.inbox.paths import _sibling_path
from mnemo.core.extract.inbox.rendering import _render_page
from mnemo.core.extract.inbox.types import ApplyResult, ExtractedPage
from mnemo.core.extract.scanner import ExtractionState, StateEntry


def _union_with_prior_sources(page: ExtractedPage, entry: StateEntry | None) -> ExtractedPage:
    """Return a copy of *page* whose ``source_files`` unions the prior
    state-entry sources with the page's freshly-extracted sources.

    Without this union, re-extracting the same rule from a different
    project's briefings overwrites ``source_files``, so the rule's
    project count never crosses ``scoping.universalThreshold`` (=2).
    This is the universal-promotion blocker observed in the v0.15 dogfood.

    Preserves order: prior sources first (so the file's history-of-mention
    stays stable), then any new sources not already present.
    """
    if entry is None or not entry.source_files:
        return page
    seen: set[str] = set()
    merged: list[str] = []
    for s in list(entry.source_files) + list(page.source_files):
        if s and s not in seen:
            seen.add(s)
            merged.append(s)
    if merged == list(page.source_files):
        return page
    return ExtractedPage(
        slug=page.slug,
        type=page.type,
        name=page.name,
        description=page.description,
        body=page.body,
        source_files=merged,
        source_hash=page.source_hash,
        stability=getattr(page, "stability", None) or "stable",
        tags=list(getattr(page, "tags", None) or []),
        enforce=getattr(page, "enforce", None),
        activates_on=getattr(page, "activates_on", None),
    )


def _handle_no_entry(
    page: ExtractedPage,
    target: Path,
    state: ExtractionState,
    run_id: str,
    content: str,
    new_written_hash: str,
    result: ApplyResult,
) -> None:
    key = f"{page.type}/{page.slug}"
    atomic_write(target, content)
    state.entries[key] = StateEntry(
        source_files=list(page.source_files),
        source_hash=page.source_hash,
        written_hash=new_written_hash,
        written_at=run_id,
        status="auto_promoted",
        last_sync=run_id,
    )
    result.auto_promoted.append(key)


def _handle_inbox_to_auto_migrate(
    page: ExtractedPage,
    entry: StateEntry,
    target: Path,
    run_id: str,
    content: str,
    new_written_hash: str,
    result: ApplyResult,
) -> None:
    """v0.2 → v0.3 migration: legacy status=inbox + target is now sacred."""
    key = f"{page.type}/{page.slug}"
    atomic_write(target, content)
    entry.mark_written(
        run_id=run_id,
        new_hash=new_written_hash,
        source_files=page.source_files,
        source_hash=page.source_hash,
        status="auto_promoted",
    )
    result.auto_promoted.append(key)


def _handle_target_missing(
    page: ExtractedPage,
    entry: StateEntry,
    target: Path,
    force: bool,
    run_id: str,
    content: str,
    new_written_hash: str,
    result: ApplyResult,
) -> None:
    """status was auto_promoted but user deleted the sacred file → dismissed."""
    key = f"{page.type}/{page.slug}"
    if force:
        atomic_write(target, content)
        entry.mark_written(
            run_id=run_id,
            new_hash=new_written_hash,
            source_files=page.source_files,
            source_hash=page.source_hash,
            status="auto_promoted",
        )
        result.auto_promoted.append(key)
    else:
        entry.status = "dismissed"
        result.dismissed_skipped.append(key)


def _handle_target_exists(
    page: ExtractedPage,
    entry: StateEntry,
    target: Path,
    vault_root: Path,
    run_id: str,
    content: str,
    new_written_hash: str,
    result: ApplyResult,
) -> None:
    """target exists — check if user edited it."""
    key = f"{page.type}/{page.slug}"
    disk_hash = content_hash(target)
    if disk_hash == entry.written_hash:
        atomic_write(target, content)
        entry.mark_written(
            run_id=run_id,
            new_hash=new_written_hash,
            source_files=page.source_files,
            source_hash=page.source_hash,
            status="auto_promoted",
        )
        result.overwrite_safe.append(key)
    else:
        # user edited the sacred file — bounce sibling back into _inbox/
        sibling = _sibling_path(target, vault_root)
        atomic_write(sibling, content)
        result.sibling_bounced.append((key, str(sibling)))


def _apply_auto_promoted(
    page: ExtractedPage,
    entry: StateEntry | None,
    target: Path,
    vault_root: Path,
    state: ExtractionState,
    run_id: str,
    force: bool,
    result: ApplyResult,
) -> None:
    # Union with prior state-entry sources so re-extracting the same rule
    # from a different project's briefings accumulates project attribution
    # (drives universal-promotion threshold). See ``_union_with_prior_sources``
    # docstring for the v0.15 dogfood finding.
    page = _union_with_prior_sources(page, entry)
    key = f"{page.type}/{page.slug}"
    content = _render_page(page, run_id=run_id, auto_promoted=True)
    new_written_hash = content_hash(content)

    if entry is None:
        _handle_no_entry(page, target, state, run_id, content, new_written_hash, result)
        return

    if entry.status == "dismissed" and not force:
        result.dismissed_skipped.append(key)
        return

    if entry.status == "inbox" and not target.exists():
        _handle_inbox_to_auto_migrate(
            page, entry, target, run_id, content, new_written_hash, result,
        )
        return

    if not target.exists():
        _handle_target_missing(
            page, entry, target, force, run_id, content, new_written_hash, result,
        )
        return

    _handle_target_exists(
        page, entry, target, vault_root, run_id, content, new_written_hash, result,
    )
