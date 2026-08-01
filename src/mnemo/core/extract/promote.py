"""Project-type 1:1 promotion (no LLM, no clustering, direct to shared/project/).

Exception: backfill-origin pages stage in ``shared/_inbox/project/`` instead —
they are reconstructed from archived transcripts and need a human to confirm
them before they reach the sacred dir.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mnemo.core.backfill.origin import ORIGIN_LINE, is_backfill_frontmatter
from mnemo.core.extract.inbox import ApplyResult, ExtractionIOError
from mnemo.core.extract.inbox.io import atomic_write, content_hash
from mnemo.core.extract.scanner import ExtractionState, MemoryFile, StateEntry
from mnemo.core.extract.source_paths import vault_relative_source


def _project_slug(file: MemoryFile) -> str:
    return f"{file.agent}__{file.slug}"


def _is_backfill(file: MemoryFile) -> bool:
    """True when this memory file was reconstructed by cold-start backfill.

    Harvest nests the stamp under ``metadata:``; ``scanner.parse_frontmatter``
    is a flat reader that lifts it to the top level. The shared predicate
    accepts either spelling, so this does not depend on which parser filled
    ``file.frontmatter``.
    """
    return is_backfill_frontmatter(file.frontmatter)


def _target_path(vault_root: Path, file: MemoryFile) -> Path:
    # Origin gate: project-type pages are the one extraction path that writes
    # straight to the sacred dir with no _inbox hop. Backfill-origin pages are
    # LLM reconstructions of archived transcripts, so they stage for review
    # like every other backfill page (see inbox/paths._target_path_for_page).
    if _is_backfill(file):
        return vault_root / "shared" / "_inbox" / "project" / f"{_project_slug(file)}.md"
    return vault_root / "shared" / "project" / f"{_project_slug(file)}.md"


def _render_project_page(file: MemoryFile, *, run_id: str) -> str:
    # TOP-LEVEL, not nested under `metadata:` — that is the one spelling both
    # frontmatter parsers in this codebase agree on (see backfill/origin.py).
    # Pinned at the text level by test_extract_promote_backfill.py; a
    # parser-level assertion cannot tell the two spellings apart.
    origin_line = ORIGIN_LINE if _is_backfill(file) else ""
    return (
        "---\n"
        f"name: {file.frontmatter.get('name', file.slug)}\n"
        f"description: {file.frontmatter.get('description', '')}\n"
        "type: project\n"
        "runtime: false\n"
        f"{origin_line}"
        f"agent: {file.agent}\n"
        f"promoted_at: {run_id}\n"
        f"extraction_run: {run_id}\n"
        "sources:\n"
        f"  - {file.path}\n"
        "---\n\n"
        f"{file.body}"
    )


def promote_projects(
    files: list[MemoryFile],
    state: ExtractionState,
    vault_root: Path,
    *,
    run_id: str | None = None,
    force: bool = False,
) -> ApplyResult:
    run_id = run_id or datetime.now().isoformat(timespec="seconds")
    result = ApplyResult()

    for file in files:
        key = f"project/{_project_slug(file)}"
        entry = state.entries.get(key)
        target = _target_path(vault_root, file)
        # "direct" means "lives in shared/<type>/". A staged page does not, so
        # it records the same "inbox" status every other _inbox page uses —
        # readers that look for the promoted file (the universal reconciler,
        # doctor) would otherwise look in the wrong place. What distinguishes a
        # staged backfill page from a live one is the file's `origin` key, not
        # the status.
        written_status = "inbox" if _is_backfill(file) else "direct"

        if entry is not None and entry.source_hash == file.source_hash and not force:
            result.unchanged_skipped.append(key)
            continue

        content = _render_project_page(file, run_id=run_id)
        new_written_hash = content_hash(content)

        if entry is None:
            atomic_write(target, content)
            state.entries[key] = StateEntry(
                source_files=[vault_relative_source(file.path, vault_root)],
                source_hash=file.source_hash,
                written_hash=new_written_hash,
                written_at=run_id,
                status=written_status,
            )
            result.written_fresh.append(key)
            continue

        # Entry exists; source changed
        if not target.exists():
            if force:
                atomic_write(target, content)
                entry.source_files = [vault_relative_source(file.path, vault_root)]
                entry.source_hash = file.source_hash
                entry.written_hash = new_written_hash
                entry.written_at = run_id
                entry.status = written_status
                result.written_fresh.append(key)
            else:
                entry.status = "dismissed"
                result.dismissed_skipped.append(key)
            continue

        disk_hash = content_hash(target)
        if disk_hash == entry.written_hash:
            atomic_write(target, content)
            entry.source_files = [vault_relative_source(file.path, vault_root)]
            entry.source_hash = file.source_hash
            entry.written_hash = new_written_hash
            entry.written_at = run_id
            result.overwrite_safe.append(key)
        else:
            sibling = target.with_name(f"{_project_slug(file)}.proposed.md")
            atomic_write(sibling, content)
            result.sibling_proposed.append((key, str(sibling)))

    return result
