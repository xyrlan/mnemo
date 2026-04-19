"""Apply branch for pages whose target lives in shared/_inbox/<type>/.

Split from the 96-line ``_apply_inbox`` in the pre-v0.9 ``inbox.py``
monolith into per-status helpers (PR I):

- :func:`_handle_no_entry`        — first-time fresh write
- :func:`_handle_dismissed`       — user previously dismissed
- :func:`_handle_promoted`        — page was already promoted
- :func:`_handle_inbox_status`    — the v0.2 main case (target exists or not)

The public entry point :func:`_apply_inbox` keeps its original name +
signature so :mod:`apply` can register it as a status handler.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.extract.inbox.io import atomic_write, content_hash
from mnemo.core.extract.inbox.paths import _promoted_path, _sibling_path
from mnemo.core.extract.inbox.rendering import _render_page
from mnemo.core.extract.inbox.types import ApplyResult, ExtractedPage
from mnemo.core.extract.scanner import ExtractionState, StateEntry


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
        status="inbox",
        last_sync=run_id,
    )
    result.written_fresh.append(key)


def _handle_dismissed(
    page: ExtractedPage,
    target: Path,
    state: ExtractionState,
    force: bool,
    run_id: str,
    content: str,
    new_written_hash: str,
    result: ApplyResult,
) -> None:
    key = f"{page.type}/{page.slug}"
    if force:
        atomic_write(target, content)
        state.entries[key] = StateEntry(
            source_files=list(page.source_files),
            source_hash=page.source_hash,
            written_hash=new_written_hash,
            written_at=run_id,
            status="inbox",
            last_sync=run_id,
        )
        result.written_fresh.append(key)
    else:
        result.dismissed_skipped.append(key)


def _handle_promoted(
    page: ExtractedPage,
    entry: StateEntry,
    target: Path,
    promoted_file: Path,
    run_id: str,
    content: str,
    result: ApplyResult,
) -> None:
    key = f"{page.type}/{page.slug}"
    if promoted_file.exists():
        update_file = target.with_name(f"{page.slug}.update-proposed.md")
        atomic_write(update_file, content)
        entry.source_hash = page.source_hash
        entry.source_files = list(page.source_files)
        result.update_proposed.append(key)
    else:
        entry.status = "dismissed"
        result.dismissed_skipped.append(key)


def _handle_inbox_status(
    page: ExtractedPage,
    entry: StateEntry,
    target: Path,
    vault_root: Path,
    promoted_file: Path,
    force: bool,
    run_id: str,
    content: str,
    new_written_hash: str,
    result: ApplyResult,
) -> None:
    """status == 'inbox' (the v0.2 main case)."""
    key = f"{page.type}/{page.slug}"
    if target.exists():
        disk_hash = content_hash(target)
        if disk_hash == entry.written_hash:
            atomic_write(target, content)
            entry.mark_written(
                run_id=run_id,
                new_hash=new_written_hash,
                source_files=page.source_files,
                source_hash=page.source_hash,
            )
            result.overwrite_safe.append(key)
        else:
            sibling = _sibling_path(target, vault_root)
            atomic_write(sibling, content)
            result.sibling_proposed.append((key, str(sibling)))
    else:
        if promoted_file.exists():
            entry.status = "promoted"
            update_file = target.with_name(f"{page.slug}.update-proposed.md")
            atomic_write(update_file, content)
            entry.source_hash = page.source_hash
            entry.source_files = list(page.source_files)
            result.update_proposed.append(key)
        elif force:
            atomic_write(target, content)
            entry.mark_written(
                run_id=run_id,
                new_hash=new_written_hash,
                source_files=page.source_files,
                source_hash=page.source_hash,
                status="inbox",
            )
            result.written_fresh.append(key)
        else:
            entry.status = "dismissed"
            result.dismissed_skipped.append(key)


def _apply_inbox(
    page: ExtractedPage,
    entry: StateEntry | None,
    target: Path,
    vault_root: Path,
    state: ExtractionState,
    run_id: str,
    force: bool,
    result: ApplyResult,
) -> None:
    """v0.2 _inbox/ branch, unchanged in behavior."""
    content = _render_page(page, run_id=run_id, auto_promoted=False)
    new_written_hash = content_hash(content)
    promoted_file = _promoted_path(vault_root, page)

    if entry is None:
        _handle_no_entry(page, target, state, run_id, content, new_written_hash, result)
        return

    status = entry.status

    if status == "dismissed":
        _handle_dismissed(
            page, target, state, force, run_id, content, new_written_hash, result,
        )
        return

    if status == "promoted":
        _handle_promoted(page, entry, target, promoted_file, run_id, content, result)
        return

    _handle_inbox_status(
        page, entry, target, vault_root, promoted_file, force,
        run_id, content, new_written_hash, result,
    )
