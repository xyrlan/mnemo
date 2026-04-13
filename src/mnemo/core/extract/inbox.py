"""State machine + atomic writes for v0.2 extraction inbox.

Implements the decision table from spec §5.4 (cluster types) using typed
exceptions only. KeyboardInterrupt must propagate — never use
`except Exception` in this file.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from mnemo.core.extract.scanner import ExtractionState, StateEntry

SCHEMA_VERSION = 2


class ExtractionIOError(OSError):
    """Filesystem failure during extraction write."""


class StateSchemaError(Exception):
    """Unknown or incompatible state file schema version."""


@dataclass
class ExtractedPage:
    slug: str
    type: str
    name: str
    description: str
    body: str
    source_files: list[str]
    source_hash: str


@dataclass
class ApplyResult:
    written_fresh: list[str] = field(default_factory=list)
    overwrite_safe: list[str] = field(default_factory=list)
    sibling_proposed: list[tuple[str, str]] = field(default_factory=list)
    update_proposed: list[str] = field(default_factory=list)
    dismissed_skipped: list[str] = field(default_factory=list)
    unchanged_skipped: list[str] = field(default_factory=list)
    auto_promoted: list[str] = field(default_factory=list)
    sibling_bounced: list[tuple[str, str]] = field(default_factory=list)
    upgrade_proposed: list[tuple[str, str]] = field(default_factory=list)


def _file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content.encode("utf-8"))
        os.replace(tmp, path)
    except OSError as exc:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise ExtractionIOError(f"failed to write {path}: {exc}") from exc


def _render_page(page: ExtractedPage, *, run_id: str, auto_promoted: bool = False) -> str:
    sources_yaml = "\n".join(f"  - {s}" for s in page.source_files)
    if auto_promoted:
        extras = f"last_sync: {run_id}\n"
        tag = "auto-promoted"
    else:
        extras = ""
        tag = "needs-review"
    return (
        "---\n"
        f"name: {page.name}\n"
        f"description: {page.description}\n"
        f"type: {page.type}\n"
        f"extracted_at: {run_id}\n"
        f"extraction_run: {run_id}\n"
        f"{extras}"
        "sources:\n"
        f"{sources_yaml}\n"
        "tags:\n"
        f"  - {tag}\n"
        "---\n\n"
        f"{page.body}\n"
    )


def _inbox_path(vault_root: Path, page: ExtractedPage) -> Path:
    return vault_root / "shared" / "_inbox" / page.type / f"{page.slug}.md"


def _promoted_path(vault_root: Path, page: ExtractedPage) -> Path:
    return vault_root / "shared" / page.type / f"{page.slug}.md"


def _target_path_for_page(page: ExtractedPage, vault_root: Path) -> Path:
    """Return the filesystem target for a page based on its source count.

    Single-source pages go directly to the sacred dir (auto-promote).
    Multi-source pages stage in _inbox/ for review.
    """
    if len(page.source_files) == 1:
        return vault_root / "shared" / page.type / f"{page.slug}.md"
    return vault_root / "shared" / "_inbox" / page.type / f"{page.slug}.md"


def _is_auto_promoted_target(target: Path, vault_root: Path) -> bool:
    """True if the target is inside shared/<type>/ (not shared/_inbox/)."""
    try:
        rel = target.relative_to(vault_root / "shared")
    except ValueError:
        return False
    parts = rel.parts
    if not parts:
        return False
    return parts[0] != "_inbox"


def _sibling_path(target: Path, vault_root: Path) -> Path:
    """Where does a .proposed.md sibling for this target live?

    Auto-promoted targets (in shared/<type>/) bounce their siblings back into
    shared/_inbox/<type>/ so the sacred dir stays free of plugin artifacts.
    _inbox/ targets keep siblings adjacent (v0.2 behavior).
    """
    if _is_auto_promoted_target(target, vault_root):
        page_type = target.parent.name
        slug = target.stem
        return vault_root / "shared" / "_inbox" / page_type / f"{slug}.proposed.md"
    return target.parent / f"{target.stem}.proposed.md"


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
        merged.append(ExtractedPage(
            slug=chosen.slug,
            type=chosen.type,
            name=chosen.name,
            description=chosen.description,
            body=chosen.body,
            source_files=all_sources,
            source_hash=chosen.source_hash,
        ))
    return merged


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
        key = f"{page.type}/{page.slug}"
        entry = state.entries.get(key)
        inbox_file = _inbox_path(vault_root, page)
        promoted_file = _promoted_path(vault_root, page)

        # Row 1: source_hash unchanged
        if entry is not None and entry.source_hash == page.source_hash and not force:
            result.unchanged_skipped.append(key)
            continue

        content = _render_page(page, run_id=run_id)
        new_written_hash = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Row 2: fresh (no entry)
        if entry is None:
            _atomic_write(inbox_file, content)
            state.entries[key] = StateEntry(
                source_files=list(page.source_files),
                source_hash=page.source_hash,
                written_hash=new_written_hash,
                written_at=run_id,
                status="inbox",
            )
            result.written_fresh.append(key)
            continue

        # Entry exists, source changed (or force)
        status = entry.status

        if status == "dismissed":
            if force:
                _atomic_write(inbox_file, content)
                state.entries[key] = StateEntry(
                    source_files=list(page.source_files),
                    source_hash=page.source_hash,
                    written_hash=new_written_hash,
                    written_at=run_id,
                    status="inbox",
                )
                result.written_fresh.append(key)
            else:
                result.dismissed_skipped.append(key)
            continue

        if status == "promoted":
            if promoted_file.exists():
                update_file = inbox_file.with_name(f"{page.slug}.update-proposed.md")
                _atomic_write(update_file, content)
                # Entry stays promoted; record source_hash so next run doesn't re-fire
                entry.source_hash = page.source_hash
                entry.source_files = list(page.source_files)
                result.update_proposed.append(key)
            else:
                # Promoted file was deleted by user — treat as dismissed
                entry.status = "dismissed"
                result.dismissed_skipped.append(key)
            continue

        # status == "inbox" (the main case)
        if inbox_file.exists():
            disk_hash = _file_hash(inbox_file)
            if disk_hash == entry.written_hash:
                # overwrite_safe
                _atomic_write(inbox_file, content)
                entry.source_files = list(page.source_files)
                entry.source_hash = page.source_hash
                entry.written_hash = new_written_hash
                entry.written_at = run_id
                result.overwrite_safe.append(key)
            else:
                # user edited — write sibling
                sibling = inbox_file.with_name(f"{page.slug}.proposed.md")
                _atomic_write(sibling, content)
                result.sibling_proposed.append((key, str(sibling)))
        else:
            # inbox file disappeared; check shared/
            if promoted_file.exists():
                entry.status = "promoted"
                update_file = inbox_file.with_name(f"{page.slug}.update-proposed.md")
                _atomic_write(update_file, content)
                entry.source_hash = page.source_hash
                entry.source_files = list(page.source_files)
                result.update_proposed.append(key)
            elif force:
                # Force resurrects: same-run detection of deletion + force means write fresh.
                _atomic_write(inbox_file, content)
                entry.source_files = list(page.source_files)
                entry.source_hash = page.source_hash
                entry.written_hash = new_written_hash
                entry.written_at = run_id
                entry.status = "inbox"
                result.written_fresh.append(key)
            else:
                entry.status = "dismissed"
                result.dismissed_skipped.append(key)

    return result


def atomic_write_state(state: ExtractionState, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "last_run": state.last_run,
        "entries": {
            k: {
                "source_files": v.source_files,
                "source_hash": v.source_hash,
                "written_hash": v.written_hash,
                "written_at": v.written_at,
                "last_sync": v.last_sync,
                "status": v.status,
            }
            for k, v in state.entries.items()
        },
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(json.dumps(payload, indent=2).encode("utf-8"))
        os.replace(tmp, path)
    except OSError as exc:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise ExtractionIOError(f"failed to write state file: {exc}") from exc


def load_state(path: Path) -> ExtractionState:
    if not path.exists():
        return ExtractionState(last_run=None, entries={}, schema_version=SCHEMA_VERSION)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Back up and return empty
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        try:
            path.rename(path.with_name(f"{path.name}.bak.{stamp}"))
        except OSError:
            pass
        return ExtractionState(last_run=None, entries={}, schema_version=SCHEMA_VERSION)

    version = int(payload.get("schema_version", 0) or 0)
    if version > SCHEMA_VERSION:
        raise StateSchemaError(
            f"state file schema_version={version} was written by a newer mnemo version"
        )
    if version < 1:
        raise StateSchemaError(
            f"state file schema_version={version}, this mnemo supports {SCHEMA_VERSION}"
        )

    entries: dict[str, StateEntry] = {}
    for k, v in payload.get("entries", {}).items():
        written_at = str(v.get("written_at") or "")
        last_sync = str(v.get("last_sync") or written_at)
        entries[k] = StateEntry(
            source_files=list(v.get("source_files") or []),
            source_hash=str(v.get("source_hash") or ""),
            written_hash=str(v.get("written_hash") or ""),
            written_at=written_at,
            status=str(v.get("status") or "inbox"),
            last_sync=last_sync,
        )
    return ExtractionState(
        last_run=payload.get("last_run"),
        entries=entries,
        schema_version=SCHEMA_VERSION,
    )
