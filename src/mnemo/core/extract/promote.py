"""Project-type 1:1 promotion (no LLM, no clustering, direct to shared/project/).

Exception: backfill-origin pages stage in ``shared/_inbox/project/`` instead —
they are reconstructed from archived transcripts and need a human to confirm
them before they reach the sacred dir.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mnemo.core.backfill.origin import (
    ORIGIN_LINE,
    is_backfill_entry,
    is_backfill_frontmatter,
    is_backfill_markdown,
)
from mnemo.core.extract.inbox import ApplyResult, ExtractionIOError
from mnemo.core.extract.inbox.io import atomic_write, content_hash
from mnemo.core.extract.scanner import (
    SACRED_STATUSES,
    ExtractionState,
    MemoryFile,
    StateEntry,
)
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


def _sticky_backfill(
    file: MemoryFile, entry: StateEntry | None, vault_root: Path,
) -> bool:
    """Backfill origin for a project page: from the file, remembered, or staged.

    Three readings, cheapest first, matching ``apply._resolve_sticky_origin``:

    1. the source memory file's own stamp — normally kept forever, so unlike
       the cluster pipeline this one does not usually lose the answer;
    2. ``StateEntry.origin_backfill``, so stripping the stamp out of the source
       file (by hand, or by some future rewriter) cannot re-open the door on a
       page already staged for review;
    3. the staged ``shared/_inbox/project/<slug>.md``, for vaults whose state
       file predates the field. Without this leg a legacy vault whose project
       source had lost its stamp had no way back — and ``project`` is the type
       backfill produces most, so that was the highest-volume asymmetry
       against the cluster path (Task 9b review).
    """
    if _is_backfill(file) or is_backfill_entry(entry):
        return True
    return is_backfill_markdown(
        vault_root / "shared" / "_inbox" / "project" / f"{_project_slug(file)}.md"
    )


def _target_path(
    vault_root: Path, file: MemoryFile, backfill: bool | None = None,
) -> Path:
    # Origin gate: project-type pages are the one extraction path that writes
    # straight to the sacred dir with no _inbox hop. Backfill-origin pages are
    # LLM reconstructions of archived transcripts, so they stage for review
    # like every other backfill page (see inbox/paths._target_path_for_page).
    # ``backfill`` defaults to the file's own stamp; ``promote_projects`` passes
    # the sticky answer, which also honours the state entry.
    if backfill is None:
        backfill = _is_backfill(file)
    if backfill:
        return vault_root / "shared" / "_inbox" / "project" / f"{_project_slug(file)}.md"
    return vault_root / "shared" / "project" / f"{_project_slug(file)}.md"


def _render_project_page(
    file: MemoryFile, *, run_id: str, backfill: bool | None = None,
) -> str:
    # TOP-LEVEL, not nested under `metadata:` — that is the one spelling both
    # frontmatter parsers in this codebase agree on (see backfill/origin.py).
    # Pinned at the text level by test_extract_promote_backfill.py; a
    # parser-level assertion cannot tell the two spellings apart.
    is_backfill = _is_backfill(file) if backfill is None else backfill
    origin_line = ORIGIN_LINE if is_backfill else ""
    return (
        "---\n"
        f"name: {file.frontmatter.get('name', file.slug)}\n"
        # #114: the composite is the state key, the ledger slug and the file
        # stem; writing it here keeps derive_rule_slug from falling through
        # to the display name for the reflex/activation indexes.
        f"slug: {_project_slug(file)}\n"
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
        backfill = _sticky_backfill(file, entry, vault_root)
        if (
            backfill
            and entry is not None
            and entry.status not in SACRED_STATUSES
        ):
            # Make it stick before any branch below can return early — but
            # never for a page that already lives in shared/project/. There is
            # no staged page to protect there, and a permanent stamp on such an
            # entry is worse here than in the cluster pipeline: every later run
            # routes to an _inbox target that does not exist and falls through
            # to status="dismissed", so the page is dropped with no _inbox
            # copy, no .proposed.md, and a stale file left in the sacred dir
            # (Task 9b review). Same guard as
            # ``inbox/apply._stamp_entry_origin``.
            entry.origin_backfill = True
        target = _target_path(vault_root, file, backfill)
        # "direct" means "lives in shared/<type>/". A staged page does not, so
        # it records the same "inbox" status every other _inbox page uses —
        # readers that look for the promoted file (the universal reconciler,
        # doctor) would otherwise look in the wrong place. What distinguishes a
        # staged backfill page from a live one is the file's `origin` key, not
        # the status.
        written_status = "inbox" if backfill else "direct"

        if entry is not None and entry.source_hash == file.source_hash and not force:
            result.unchanged_skipped.append(key)
            continue

        content = _render_project_page(file, run_id=run_id, backfill=backfill)
        new_written_hash = content_hash(content)

        if entry is None:
            atomic_write(target, content)
            state.entries[key] = StateEntry(
                source_files=[vault_relative_source(file.path, vault_root)],
                source_hash=file.source_hash,
                written_hash=new_written_hash,
                written_at=run_id,
                status=written_status,
                origin_backfill=backfill,
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
