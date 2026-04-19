"""Project-type 1:1 promotion (no LLM, no clustering, direct to shared/project/)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mnemo.core.extract.inbox import ApplyResult, ExtractionIOError
from mnemo.core.extract.inbox.io import atomic_write, content_hash
from mnemo.core.extract.scanner import ExtractionState, MemoryFile, StateEntry


def _project_slug(file: MemoryFile) -> str:
    return f"{file.agent}__{file.slug}"


def _target_path(vault_root: Path, file: MemoryFile) -> Path:
    return vault_root / "shared" / "project" / f"{_project_slug(file)}.md"


def _render_project_page(file: MemoryFile, *, run_id: str) -> str:
    return (
        "---\n"
        f"name: {file.frontmatter.get('name', file.slug)}\n"
        f"description: {file.frontmatter.get('description', '')}\n"
        "type: project\n"
        "runtime: false\n"
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

        if entry is not None and entry.source_hash == file.source_hash and not force:
            result.unchanged_skipped.append(key)
            continue

        content = _render_project_page(file, run_id=run_id)
        new_written_hash = content_hash(content)

        if entry is None:
            atomic_write(target, content)
            state.entries[key] = StateEntry(
                source_files=[str(file.path)],
                source_hash=file.source_hash,
                written_hash=new_written_hash,
                written_at=run_id,
                status="direct",
            )
            result.written_fresh.append(key)
            continue

        # Entry exists; source changed
        if not target.exists():
            if force:
                atomic_write(target, content)
                entry.source_files = [str(file.path)]
                entry.source_hash = file.source_hash
                entry.written_hash = new_written_hash
                entry.written_at = run_id
                entry.status = "direct"
                result.written_fresh.append(key)
            else:
                entry.status = "dismissed"
                result.dismissed_skipped.append(key)
            continue

        disk_hash = content_hash(target)
        if disk_hash == entry.written_hash:
            atomic_write(target, content)
            entry.source_files = [str(file.path)]
            entry.source_hash = file.source_hash
            entry.written_hash = new_written_hash
            entry.written_at = run_id
            result.overwrite_safe.append(key)
        else:
            sibling = target.with_name(f"{_project_slug(file)}.proposed.md")
            atomic_write(sibling, content)
            result.sibling_proposed.append((key, str(sibling)))

    return result
