"""mnemo v0.2 LLM extraction pipeline — public entry point."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from mnemo.core import errors, locks, llm, paths
from mnemo.core.extract import inbox, promote, prompts, scanner
from mnemo.core.extract.inbox import ExtractionIOError  # re-export
from mnemo.core.extract.scanner import ExtractionState


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
        out.append(inbox.ExtractedPage(
            slug=slug,
            type=str(rp.get("type") or default_type),
            name=str(rp.get("name") or slug),
            description=str(rp.get("description") or ""),
            body=body,
            source_files=source_files,
            source_hash=src_hash,
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


def run_extraction(cfg: dict, *, dry_run: bool = False, force: bool = False) -> ExtractionSummary:
    start = time.monotonic()
    vault_root = paths.vault_root(cfg)
    state_path = vault_root / ".mnemo" / "extraction-state.json"
    lock_path = vault_root / ".mnemo" / "extract.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    summary = ExtractionSummary()
    run_id = datetime.now().isoformat(timespec="seconds")

    with locks.try_lock(lock_path) as acquired:
        if not acquired:
            raise ExtractionIOError(
                "another extraction is in progress (lock held); try again later"
            )

        state = inbox.load_state(state_path)
        scan_result = scanner.scan(vault_root, state)

        if dry_run:
            return _print_estimate(scan_result, cfg)

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
            # Track which files were successfully processed (chunk did not fail)
            processed_files: list[scanner.MemoryFile] = []
            for chunk in prompts.chunks_for(files, chunk_size):
                prompt_text = builder(chunk)
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
            # We create/update entries under the file's own scanner key here.
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

        summary.wall_time_s = time.monotonic() - start

    return summary
