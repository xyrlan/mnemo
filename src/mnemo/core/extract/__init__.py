"""mnemo v0.2 LLM extraction pipeline — public entry point."""
from __future__ import annotations

import hashlib
import json as _json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from mnemo.core import dashboard, errors, locks, llm, paths
from mnemo.core.extract import inbox, promote, prompts, scanner
from mnemo.core.extract.inbox import ExtractionIOError  # re-export
from mnemo.core.extract.scanner import ExtractionState
from mnemo.core.filters import MANAGED_TAGS


def _sanitize_llm_tags(raw: object) -> list[str]:
    """Normalize an LLM-emitted ``tags`` field into a clean kebab-case list.

    - Must be a list; anything else → empty.
    - Strings only; strip, lowercase, drop empties.
    - Reserved managed markers (``auto-promoted``, ``needs-review``, etc.) are
      silently stripped so the LLM can't hijack system tags even if it copies
      them from the few-shot or an existing page.
    - Order is preserved, duplicates removed.
    - Capped at 5 tags per page (sanity limit against LLM over-emission).
    """
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        t = item.strip().lower()
        if not t:
            continue
        if t in MANAGED_TAGS:
            continue
        if t in out:
            continue
        out.append(t)
        if len(out) >= 5:
            break
    return out


@dataclass
class ExtractionSummary:
    projects_promoted: int = 0
    pages_written: int = 0
    sibling_proposed: int = 0
    update_proposed: int = 0
    unchanged_skipped: int = 0
    dismissed_skipped: int = 0
    failed_chunks: int = 0
    llm_calls: int = 0
    wall_time_s: float = 0.0
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    all_calls_subscription: bool = True
    conflicts: list[tuple[str, str]] = field(default_factory=list)
    auto_promoted: int = 0
    sibling_bounced: int = 0
    upgrade_proposed: int = 0
    mode: str = "manual"


def _merge_apply(result: inbox.ApplyResult, summary: ExtractionSummary) -> None:
    summary.pages_written += (
        len(result.written_fresh)
        + len(result.overwrite_safe)
        + len(result.auto_promoted)
    )
    summary.sibling_proposed += len(result.sibling_proposed)
    summary.update_proposed += len(result.update_proposed)
    summary.unchanged_skipped += len(result.unchanged_skipped)
    summary.dismissed_skipped += len(result.dismissed_skipped)
    summary.auto_promoted += len(result.auto_promoted)
    summary.sibling_bounced += len(result.sibling_bounced)
    summary.upgrade_proposed += len(result.upgrade_proposed)
    summary.conflicts.extend(result.sibling_proposed)
    summary.conflicts.extend(result.sibling_bounced)
    summary.conflicts.extend(result.upgrade_proposed)


def _parse_pages_from_response(text: str, default_type: str) -> list[inbox.ExtractedPage]:
    payload = llm._parse_llm_json(text)
    raw_pages = payload.get("pages", [])
    if not isinstance(raw_pages, list):
        return []
    out: list[inbox.ExtractedPage] = []
    for rp in raw_pages:
        if not isinstance(rp, dict):
            continue
        slug = scanner._normalize_slug(str(rp.get("slug") or ""))
        if not slug:
            continue
        body = str(rp.get("body") or "")
        if not body.strip():
            continue
        source_files = [s for s in (rp.get("source_files") or []) if isinstance(s, str)]
        if not source_files:
            continue
        src_hash = "sha256:" + hashlib.sha256(
            ("|".join(sorted(source_files)) + "||" + body).encode("utf-8")
        ).hexdigest()
        stability_raw = str(rp.get("stability") or "stable").strip().lower()
        stability = stability_raw if stability_raw in ("stable", "evolving") else "stable"
        tags = _sanitize_llm_tags(rp.get("tags"))
        out.append(inbox.ExtractedPage(
            slug=slug,
            type=str(rp.get("type") or default_type),
            name=str(rp.get("name") or slug),
            description=str(rp.get("description") or ""),
            body=body,
            source_files=source_files,
            source_hash=src_hash,
            stability=stability,
            tags=tags,
        ))
    return out


def _print_estimate(scan: scanner.ScanResult, cfg: dict) -> ExtractionSummary:
    chunk_size = cfg["extraction"]["chunkSize"]
    total_calls = 0
    for t in ("feedback", "user", "reference"):
        n = len(scan.by_type.get(t, []))
        if n > 0:
            total_calls += (n + chunk_size - 1) // chunk_size
    projects = len(scan.by_type.get("project", []))
    dirty = len(scan.dirty_files)
    print(
        f"[dry-run] scan: {projects} projects, "
        f"{sum(len(scan.by_type[t]) for t in ('feedback','user','reference'))} cluster-type files, "
        f"{dirty} dirty"
    )
    print(f"[dry-run] would make {total_calls} LLM calls (model={cfg['extraction']['model']})")
    print(f"[dry-run] no writes; no state changes")
    return ExtractionSummary()


def _atomic_write_last_auto_run(
    path: Path,
    *,
    run_id: str,
    started_at: str,
    finished_at: str,
    exit_code: int,
    summary: ExtractionSummary,
    error: dict | None,
) -> None:
    payload = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "mode": summary.mode,
        "exit_code": exit_code,
        "summary": {
            k: v
            for k, v in asdict(summary).items()
            if k != "conflicts"
        },
        "error": error,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(_json.dumps(payload, indent=2).encode("utf-8"))
        os.replace(tmp, path)
    except OSError as exc:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise ExtractionIOError(f"failed to write last-auto-run.json: {exc}") from exc


_FORCE_WIPE_TYPES = ("feedback", "user", "reference")


def _force_clear_inbox_cluster_dirs(vault_root: Path) -> None:
    """Delete every .md directly under shared/_inbox/<cluster_type>/.

    Called only when --force is set, before any cluster extraction runs.
    Wipes slug-drift duplicates from prior force runs (see v0.3.1 spec §3b).
    Intentionally leaves non-cluster subdirs (e.g. _inbox/project/) alone.
    """
    inbox_root = vault_root / "shared" / "_inbox"
    if not inbox_root.is_dir():
        return
    for type_name in _FORCE_WIPE_TYPES:
        type_dir = inbox_root / type_name
        if not type_dir.is_dir():
            continue
        for md in type_dir.glob("*.md"):
            try:
                md.unlink()
            except OSError:
                continue


def _run_extraction_body(
    cfg: dict,
    vault_root: Path,
    state_path: Path,
    summary: ExtractionSummary,
    *,
    run_id: str,
    dry_run: bool,
    force: bool,
) -> None:
    state = inbox.load_state(state_path)
    scan_result = scanner.scan(vault_root, state)

    if dry_run:
        _print_estimate(scan_result, cfg)
        return

    if force:
        _force_clear_inbox_cluster_dirs(vault_root)

    # Phase 1: projects (zero LLM, fastest, cannot fail from network)
    project_files = scan_result.by_type.get("project", [])
    project_result = promote.promote_projects(
        project_files, state, vault_root, run_id=run_id, force=force,
    )
    summary.projects_promoted += len(project_result.written_fresh) + len(project_result.overwrite_safe)
    _merge_apply(project_result, summary)
    state.last_run = run_id
    try:
        inbox.atomic_write_state(state, state_path)
    except ExtractionIOError as exc:
        errors.log_error(vault_root, "extract.state", exc)
        raise

    # Phase 2+: cluster types
    type_plan = [
        ("feedback", prompts.build_feedback_prompt, prompts.FEEDBACK_SYSTEM_PROMPT),
        ("user",     prompts.build_user_prompt,     prompts.USER_SYSTEM_PROMPT),
        ("reference",prompts.build_reference_prompt,prompts.REFERENCE_SYSTEM_PROMPT),
    ]
    chunk_size = cfg["extraction"]["chunkSize"]
    timeout = cfg["extraction"]["subprocessTimeout"]
    model = cfg["extraction"]["model"]

    for type_name, builder, system_prompt in type_plan:
        files = scan_result.by_type.get(type_name, [])
        if not files:
            continue

        # Filter to dirty only unless force
        if not force:
            dirty_set = set(id(f) for f in scan_result.dirty_files)
            files = [f for f in files if id(f) in dirty_set]
        if not files:
            continue

        all_pages: list[inbox.ExtractedPage] = []
        processed_files: list[scanner.MemoryFile] = []
        for chunk in prompts.chunks_for(files, chunk_size):
            prompt_text = builder(chunk, vault_root=vault_root)
            try:
                response = llm.call(
                    prompt_text,
                    system=system_prompt,
                    model=model,
                    timeout=timeout,
                )
            except (llm.LLMSubprocessError, llm.LLMParseError) as exc:
                errors.log_error(vault_root, "extract.chunk", exc)
                summary.failed_chunks += 1
                continue

            summary.llm_calls += 1
            summary.total_cost_usd += response.total_cost_usd or 0.0
            summary.total_input_tokens += response.input_tokens or 0
            summary.total_output_tokens += response.output_tokens or 0
            if response.api_key_source != "none":
                summary.all_calls_subscription = False

            try:
                pages = _parse_pages_from_response(response.text, type_name)
            except llm.LLMParseError as exc:
                errors.log_error(vault_root, "extract.parse", exc)
                summary.failed_chunks += 1
                continue

            all_pages.extend(pages)
            processed_files.extend(chunk)

        if all_pages:
            deduped = inbox.dedupe_by_slug(all_pages)
            apply_result = inbox.apply_pages(
                deduped, state, vault_root, run_id=run_id, force=force,
            )
            _merge_apply(apply_result, summary)

        # For every successfully processed source file, record its file-level
        # hash under its scanner key so the next scan won't mark it dirty.
        # apply_pages stores entries keyed by the LLM-chosen page slug, which
        # differs from the source file's scanner key (f"{type}/{file.slug}").
        for mf in processed_files:
            file_key = f"{type_name}/{mf.slug}"
            entry = state.entries.get(file_key)
            if entry is None:
                state.entries[file_key] = scanner.StateEntry(
                    source_files=[str(mf.path)],
                    source_hash=mf.source_hash,
                    written_hash="",
                    written_at=run_id,
                    status="inbox",
                )
            else:
                entry.source_hash = mf.source_hash

        if processed_files:
            state.last_run = run_id
            try:
                inbox.atomic_write_state(state, state_path)
            except ExtractionIOError as exc:
                errors.log_error(vault_root, "extract.state", exc)
                raise


def _cleanup_legacy_wiki_dirs(vault_root: Path) -> None:
    """v0.4: delete the fossil ``wiki/sources/`` and ``wiki/compiled/`` dirs.

    These directories only ever held plugin-managed copies of ``shared/``
    content (via the now-deleted ``promote_note``/``compile_wiki`` pair). No
    user-authored work lives there, so we wipe them on first v0.4 extract.
    Idempotent — silent after the first run.
    """
    import shutil

    wiki = vault_root / "wiki"
    removed: list[str] = []
    for name in ("sources", "compiled"):
        target = wiki / name
        if target.exists():
            shutil.rmtree(target, ignore_errors=False)
            removed.append(f"wiki/{name}")
    if removed:
        print(f"[mnemo v0.4] removed legacy dir(s): {', '.join(removed)}")
    # If wiki/ itself is now empty (no user-created files), drop it too.
    if wiki.is_dir():
        try:
            next(wiki.iterdir())
        except StopIteration:
            wiki.rmdir()


def run_extraction(
    cfg: dict,
    *,
    dry_run: bool = False,
    force: bool = False,
    background: bool = False,
) -> ExtractionSummary:
    start = time.monotonic()
    vault_root = paths.vault_root(cfg)
    state_path = vault_root / ".mnemo" / "extraction-state.json"
    lock_path = vault_root / ".mnemo" / "extract.lock"
    last_auto_run_path = vault_root / ".mnemo" / "last-auto-run.json"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    summary = ExtractionSummary()
    summary.mode = "background" if background else "manual"
    run_id = datetime.now().isoformat(timespec="seconds")
    started_at = run_id
    caught_error: BaseException | None = None

    with locks.try_lock(lock_path) as acquired:
        if not acquired:
            raise ExtractionIOError(
                "another extraction is in progress (lock held); try again later"
            )

        try:
            _run_extraction_body(
                cfg, vault_root, state_path, summary,
                run_id=run_id, dry_run=dry_run, force=force,
            )
        except (llm.LLMSubprocessError, llm.LLMParseError, ExtractionIOError) as exc:
            caught_error = exc
            if not background:
                raise
        except OSError as exc:
            caught_error = exc
            if not background:
                raise

        if not dry_run:
            try:
                _cleanup_legacy_wiki_dirs(vault_root)
            except OSError as exc:
                errors.log_error(vault_root, "extract.legacy_cleanup", exc)
            try:
                dashboard.update_home_md(cfg)
            except OSError as exc:
                # Dashboard failures must never abort extraction — log and keep going.
                errors.log_error(vault_root, "extract.dashboard", exc)

        summary.wall_time_s = time.monotonic() - start

        if background and not dry_run:
            exit_code = 0
            if summary.failed_chunks > 0:
                exit_code = 1
            if caught_error is not None:
                exit_code = 1
            error_payload: dict | None = None
            if caught_error is not None:
                error_payload = {
                    "type": type(caught_error).__name__,
                    "message": str(caught_error),
                }
            try:
                _atomic_write_last_auto_run(
                    last_auto_run_path,
                    run_id=run_id,
                    started_at=started_at,
                    finished_at=datetime.now().isoformat(timespec="seconds"),
                    exit_code=exit_code,
                    summary=summary,
                    error=error_payload,
                )
            except ExtractionIOError as exc:
                errors.log_error(vault_root, "extract.bg.summary", exc)

    return summary
