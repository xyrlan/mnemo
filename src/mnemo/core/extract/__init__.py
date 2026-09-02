"""mnemo v0.2 LLM extraction pipeline — public entry point."""
from __future__ import annotations

import hashlib
import json as _json
import os
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path

from mnemo.core import dashboard, errors, learned, locks, llm, paths
from mnemo.core.backfill.origin import (
    is_backfill_entry,
    is_backfill_frontmatter,
    is_backfill_markdown,
)
from mnemo.core.extract import evidence, inbox, promote, prompts, scanner, source_paths
from mnemo.core.extract.guards import is_prompt_echo
from mnemo.core.extract.inbox import ExtractionIOError  # re-export
from mnemo.core.extract.scanner import ExtractionState
from mnemo.core.filters import MANAGED_TAGS
from mnemo.core.redact import redact


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


def _sanitize_llm_enforce(raw: object) -> dict | None:
    """Normalize an LLM-emitted ``enforce`` block into a serializable dict.

    Shape:
        {"tool": "Bash", "deny_pattern"|"deny_patterns"|"deny_command"|"deny_commands": ..., "reason": "..."}

    We keep the sanitizer deliberately lax — full validation lives in
    rule_activation.parse_enforce_block which runs at index-build time. Here
    we only normalize enough to survive the round-trip to disk: strings,
    lists-of-strings, and the required keys. Anything weird is silently
    dropped (returning None) so a malformed LLM emission can't break the
    extraction pipeline.
    """
    if not isinstance(raw, dict) or not raw:
        return None
    tool = raw.get("tool")
    if not isinstance(tool, str) or not tool:
        return None
    out: dict = {"tool": tool}

    def _coerce_str_list(value: object) -> list[str] | None:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            cleaned = [v for v in value if isinstance(v, str) and v]
            return cleaned or None
        return None

    patterns = _coerce_str_list(raw.get("deny_pattern") or raw.get("deny_patterns"))
    if patterns:
        # Emit as deny_pattern (singular) when exactly one, else deny_patterns
        # list — keeps the frontmatter readable for the common case while
        # still round-tripping multiple values.
        if len(patterns) == 1:
            out["deny_pattern"] = patterns[0]
        else:
            out["deny_patterns"] = patterns
    commands = _coerce_str_list(raw.get("deny_command") or raw.get("deny_commands"))
    if commands:
        if len(commands) == 1:
            out["deny_command"] = commands[0]
        else:
            out["deny_commands"] = commands
    if "deny_pattern" not in out and "deny_patterns" not in out and \
       "deny_command" not in out and "deny_commands" not in out:
        return None
    reason = raw.get("reason")
    if isinstance(reason, str) and reason:
        out["reason"] = reason
    else:
        return None
    return out


def _sanitize_llm_activates_on(raw: object) -> dict | None:
    """Normalize an LLM-emitted ``activates_on`` block.

    Shape: ``{"tools": [...], "path_globs": [...]}``. Both must be non-empty
    lists of strings; otherwise return None and let the rule pass through
    without activation metadata.
    """
    if not isinstance(raw, dict) or not raw:
        return None
    tools_raw = raw.get("tools")
    if not isinstance(tools_raw, list):
        return None
    tools = [t for t in tools_raw if isinstance(t, str) and t]
    if not tools:
        return None
    globs_raw = raw.get("path_globs")
    if not isinstance(globs_raw, list):
        return None
    globs = [g for g in globs_raw if isinstance(g, str) and g]
    if not globs:
        return None
    return {"tools": tools, "path_globs": globs}


def _sanitize_llm_evidence(raw: object) -> dict | None:
    """Accept ``{"quote": non-empty str, "source": non-empty path str}``; else None.

    Whitespace (including newlines) in ``quote`` is collapsed to single
    spaces so a hostile/careless LLM quote cannot inject a bare ``---`` line
    or a bogus top-level key when the page is rendered to frontmatter.
    ``source`` must be a single path token — any whitespace rejects it
    outright rather than silently mangling a path.
    """
    if not isinstance(raw, dict):
        return None
    quote = raw.get("quote")
    source = raw.get("source")
    if not isinstance(quote, str) or not quote.strip():
        return None
    if not isinstance(source, str) or not source.strip():
        return None
    if any(c.isspace() for c in source):
        return None
    quote = " ".join(quote.split())
    if not quote:
        return None
    return {"quote": quote, "source": source.strip()}


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
    universal_promoted: int = 0
    echo_rejected: int = 0
    redactions: int = 0
    demoted_unverified: int = 0
    mode: str = "manual"


def _merge_apply(result: inbox.ApplyResult, summary: ExtractionSummary) -> None:
    summary.pages_written += (
        len(result.written_fresh)
        + len(result.overwrite_safe)
        + len(result.auto_promoted)
        + len(result.universal_promoted)
    )
    summary.sibling_proposed += len(result.sibling_proposed)
    summary.update_proposed += len(result.update_proposed)
    summary.unchanged_skipped += len(result.unchanged_skipped)
    summary.dismissed_skipped += len(result.dismissed_skipped)
    summary.auto_promoted += len(result.auto_promoted)
    summary.sibling_bounced += len(result.sibling_bounced)
    summary.upgrade_proposed += len(result.upgrade_proposed)
    summary.universal_promoted += len(result.universal_promoted)
    summary.conflicts.extend(result.sibling_proposed)
    summary.conflicts.extend(result.sibling_bounced)
    summary.conflicts.extend(result.upgrade_proposed)


def _parse_pages_from_response(
    text: str,
    default_type: str,
    *,
    backfill_sources: frozenset[str] = frozenset(),
) -> list[inbox.ExtractedPage]:
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
        enforce = _sanitize_llm_enforce(rp.get("enforce"))
        activates_on = _sanitize_llm_activates_on(rp.get("activates_on"))
        evidence = _sanitize_llm_evidence(rp.get("evidence"))
        # ``any``, not ``all``: a page mixing one live source with one
        # reconstructed source is still partly reconstructed, and under ``all``
        # it would carry origin_backfill=False, span two projects, and be
        # universally promoted into the sacred dir unreviewed.
        origin_backfill = any(s in backfill_sources for s in source_files)
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
            enforce=enforce,
            activates_on=activates_on,
            origin_backfill=origin_backfill,
            evidence=evidence,
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

    Backfill-stamped pages are exempt, for two reasons that point the same way
    (ninth bypass, Task 9b review):

    * this wipe runs *before* Phase 2, and the staged page is what
      ``apply._resolve_sticky_origin`` reads to recover an origin for a vault
      whose state file predates ``StateEntry.origin_backfill``. Delete it and
      a legacy vault's staged page is laundered into ``shared/`` by the very
      next ``--force`` run — no hand-editing, no file deletion required;
    * a staged page is a review request. Silently deleting one on ``--force``
      loses work the user was asked to look at, drift duplicate or not.
    """
    inbox_root = vault_root / "shared" / "_inbox"
    if not inbox_root.is_dir():
        return
    for type_name in _FORCE_WIPE_TYPES:
        type_dir = inbox_root / type_name
        if not type_dir.is_dir():
            continue
        for md in type_dir.glob("*.md"):
            if is_backfill_markdown(md):
                continue
            try:
                md.unlink()
            except OSError:
                continue


def _record_learned(
    vault_root: Path, run_id: str, entries: list[dict],
) -> None:
    """Append promoted pages to the learned ledger. Never aborts extraction.

    The ledger is what SessionStart announces to the user; a failure here
    costs one announcement, never a run, so it is logged and swallowed.
    """
    if not entries:
        return
    try:
        learned.record(vault_root, run_id=run_id, entries=entries)
    except Exception as exc:  # noqa: BLE001 — the ledger is never load-bearing
        errors.log_error(vault_root, "extract.learned", exc)


def _learned_entries_for_pages(
    pages: list[inbox.ExtractedPage], keys: list[str],
) -> list[dict]:
    """Ledger entries for the ``<type>/<slug>`` keys that landed live this run.

    ``keys`` are the ``auto_promoted`` / ``universal_promoted`` lists, which
    name pages by the same key ``dedupe_by_slug`` produced them under, so the
    lookup is exact rather than by slug alone (two types can share a slug).
    """
    from mnemo.core.rule_activation import projects_for_rule

    by_key = {f"{p.type}/{p.slug}": p for p in pages}
    entries: list[dict] = []
    for key in keys:
        page = by_key.get(key)
        if page is None:
            continue
        entries.append({
            "slug": page.slug,
            "type": page.type,
            "name": page.name,
            "projects": projects_for_rule(page.source_files),
            "confidence": page.confidence,
            "quote": (page.evidence or {}).get("quote"),
        })
    return entries


def _learned_entries_for_projects(
    project_files: list[scanner.MemoryFile], keys: list[str],
) -> list[dict]:
    """Ledger entries for freshly-written ``project/<slug>`` pages.

    ``promote.promote_projects`` keys its result by ``project/<agent>__<slug>``
    (see ``promote._project_slug``), so the memory files are indexed under the
    same composite rather than under ``file.slug``. A project page has no
    evidence quote and no source_files list to mine, so its project is the
    agent that owns it.
    """
    by_key = {f"project/{mf.agent}__{mf.slug}": mf for mf in project_files}
    entries: list[dict] = []
    for key in keys:
        mf = by_key.get(key)
        if mf is None:
            continue
        entries.append({
            "slug": key.split("/", 1)[1],
            "type": "project",
            "name": mf.slug,
            "projects": [mf.agent] if mf.agent else [],
            "confidence": "inferred",
            "quote": None,
        })
    return entries


def _run_extraction_body(
    cfg: dict,
    vault_root: Path,
    state_path: Path,
    summary: ExtractionSummary,
    *,
    run_id: str,
    dry_run: bool,
    force: bool,
    only: str | None = None,
) -> None:
    # The existing-rules prompt fragment caches its vault scan; a fresh run
    # must not inherit another run's view of the vault. Invariant: the cache is
    # empty or freshly cleared before the first consolidation prompt of every
    # kind — this clear covers the first one, and each `apply_pages` below
    # clears it again so the next kind sees the pages that just landed.
    # Clearing before `promote_projects` rather than after is still correct:
    # the cache fills lazily on first prompt build, and the fragment scans only
    # rule kinds, so the project pages that phase writes cannot stale it.
    prompts.existing_rules.clear_cache()
    state = inbox.load_state(state_path)
    scan_result = scanner.scan(vault_root, state)

    # `only` scopes this run to a single scanner key ("<type>/<slug>"). It is
    # what `mnemo learn` passes so the five-minute loop consolidates *this*
    # session's briefing and nothing else: a maintainer's vault can carry
    # hundreds of dirty files from other projects, and each one would be an
    # unrequested LLM call the user did not ask for and has to pay for.
    if only is not None:
        scan_result = replace(
            scan_result,
            dirty_files=[
                f for f in scan_result.dirty_files if f"{f.type}/{f.slug}" == only
            ],
        )

    if dry_run:
        _print_estimate(scan_result, cfg)
        return

    # Scoped runs never clear the whole inbox: `only` narrows this pass to one
    # file, and wiping every staged cluster page for it would destroy work the
    # user did not ask this run to touch.
    if force and only is None:
        _force_clear_inbox_cluster_dirs(vault_root)

    # Phase 1: projects (zero LLM, fastest, cannot fail from network). Skipped
    # under `only`: it is zero-LLM but still rewrites every project page in the
    # vault, which a scoped run has no business touching.
    if only is None:
        project_files = scan_result.by_type.get("project", [])
        project_result = promote.promote_projects(
            project_files, state, vault_root, run_id=run_id, force=force,
        )
        summary.projects_promoted += len(project_result.written_fresh) + len(project_result.overwrite_safe)
        _merge_apply(project_result, summary)
        _record_learned(
            vault_root, run_id,
            _learned_entries_for_projects(project_files, project_result.written_fresh),
        )
    # `last_run` is the *vault-wide* watermark the SessionEnd debounce reads
    # (`hooks.session_end._debounce_passes`). A scoped run consolidated one
    # file, not the vault, so advancing it would push the next automatic
    # extraction back a full interval every time the user typed `mnemo learn`.
    # The per-file state entries below still record what this run did see.
    if only is None:
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

        # Filter to dirty only unless force. A scoped run always filters:
        # `only` already narrowed dirty_files to the one key, and force must
        # not widen it back to the whole vault.
        if not force or only is not None:
            dirty_set = set(id(f) for f in scan_result.dirty_files)
            files = [f for f in files if id(f) in dirty_set]
        if not files:
            continue

        all_pages: list[inbox.ExtractedPage] = []
        processed_files: list[scanner.MemoryFile] = []
        for chunk in prompts.chunks_for(files, chunk_size):
            prompt_text = builder(chunk, vault_root=vault_root)
            t0 = time.perf_counter()
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
            elapsed_ms = (time.perf_counter() - t0) * 1000
            try:
                from mnemo.core.mcp import access_log as _al
                _al.record_llm_call(
                    vault_root=vault_root,
                    response=response,
                    purpose=f"consolidation:{type_name}",
                    model=model,
                    project=None,  # extraction is vault-wide, not per-project
                    agent="(extraction)",
                    elapsed_ms=elapsed_ms,
                )
            except Exception:
                pass

            summary.llm_calls += 1
            summary.total_cost_usd += response.total_cost_usd or 0.0
            summary.total_input_tokens += response.input_tokens or 0
            summary.total_output_tokens += response.output_tokens or 0
            if response.api_key_source != "none":
                summary.all_calls_subscription = False

            try:
                # Harvest nests the stamp under ``metadata:`` and
                # ``scanner.parse_frontmatter`` (a flat key: value reader)
                # lifts it to the top level; the shared predicate accepts
                # either spelling so this cannot drift on a parser change.
                chunk_backfill = frozenset(
                    source_paths.vault_relative_source(mf.path, vault_root)
                    for mf in chunk
                    if is_backfill_frontmatter(mf.frontmatter)
                )
                pages = _parse_pages_from_response(
                    response.text, type_name, backfill_sources=chunk_backfill,
                )
            except llm.LLMParseError as exc:
                errors.log_error(vault_root, "extract.parse", exc)
                summary.failed_chunks += 1
                continue

            kept: list[inbox.ExtractedPage] = []
            for p in pages:
                if is_prompt_echo(p):
                    # Demoted, never dropped: a false positive today would be
                    # permanent, silent data loss. Coerced exactly the way the
                    # evidence gate demotes an unverified feedback page, so it
                    # stages in shared/_inbox/reference/ for a human to judge.
                    summary.echo_rejected += 1
                    errors.log_error(vault_root, "extract.prompt_echo",
                                     ValueError(f"rejected prompt-echo page {p.type}/{p.slug}"))
                    p = replace(p, type="reference", confidence="inferred",
                                unverified_feedback=True, evidence=None)
                else:
                    p = evidence.verify_page(p, vault_root)
                    # Counted apart from echoes: an echo is a bad page, an
                    # unverified feedback page is a real one whose quote the
                    # gate could not find in a cited briefing.
                    if p.unverified_feedback:
                        summary.demoted_unverified += 1
                # Redaction runs AFTER verification on purpose: the evidence
                # quote is left untouched so it still matches the briefing (a
                # quote containing PII is the user's own words). Rebuilt with
                # ``replace`` rather than assigned in place — verify_page
                # returns the *same* instance for a non-feedback page, so a
                # field write would reach through to the caller's object.
                new_name, n_name = redact(p.name)
                new_desc, n_desc = redact(p.description)
                new_body, n_body = redact(p.body)
                summary.redactions += n_name + n_desc + n_body
                p = replace(p, name=new_name, description=new_desc, body=new_body)
                kept.append(p)
            all_pages.extend(kept)
            processed_files.extend(chunk)

        if all_pages:
            deduped = inbox.dedupe_by_slug(all_pages)
            apply_result = inbox.apply_pages(
                deduped, state, vault_root, run_id=run_id, force=force,
            )
            _merge_apply(apply_result, summary)
            _record_learned(
                vault_root, run_id,
                _learned_entries_for_pages(
                    deduped,
                    apply_result.auto_promoted + apply_result.universal_promoted,
                ),
            )
            # Pages just landed in the vault; the next kind's prompt must see
            # them so it can reinforce rather than mint a near-duplicate.
            prompts.existing_rules.clear_cache()

        # For every successfully processed source file, record its file-level
        # hash under its scanner key so the next scan won't mark it dirty.
        # apply_pages stores entries keyed by the LLM-chosen page slug, which
        # differs from the source file's scanner key (f"{type}/{file.slug}").
        for mf in processed_files:
            file_key = f"{type_name}/{mf.slug}"
            entry = state.entries.get(file_key)
            if entry is None:
                state.entries[file_key] = scanner.StateEntry(
                    source_files=[
                        source_paths.vault_relative_source(mf.path, vault_root)
                    ],
                    source_hash=mf.source_hash,
                    written_hash="",
                    written_at=run_id,
                    status="inbox",
                )
            else:
                entry.source_hash = mf.source_hash

        if processed_files:
            if only is None:
                state.last_run = run_id
            try:
                inbox.atomic_write_state(state, state_path)
            except ExtractionIOError as exc:
                errors.log_error(vault_root, "extract.state", exc)
                raise

    # End-of-extract reconciliation: drain orphan _inbox/<type>/<slug>.md
    # files whose state entries already cross universalThreshold. Always
    # runs (even when no cluster pages produced this run) so the backlog
    # discovered during the v0.15 dogfood clears deterministically.
    # Deliberately not recorded in the learned ledger: these promotions drain a
    # legacy backlog, not something the session just taught mnemo, and
    # announcing them would read as "newly learned" months after the fact.
    try:
        changed = _reconcile_universal_promotions(state, vault_root, run_id, summary)
    except Exception as exc:  # noqa: BLE001 — reconciler must fail-open
        errors.log_error(vault_root, "extract.reconcile_universal", exc)
        changed = 0
    if changed:
        try:
            inbox.atomic_write_state(state, state_path)
        except ExtractionIOError as exc:
            errors.log_error(vault_root, "extract.state", exc)


def _reconcile_universal_promotions(
    state: scanner.ExtractionState,
    vault_root: Path,
    run_id: str,
    summary: ExtractionSummary,
) -> int:
    """Drain ``shared/_inbox/<type>/*.md`` files whose state entry already
    crosses ``scoping.universalThreshold``.

    Runs at the tail of every extract so the legacy backlog discovered
    during the v0.15 dogfood (six rules sat in ``_inbox/feedback/`` with
    cross-project sources but no dispatch branch to move them out) clears
    without requiring those source briefings to be re-mined.

    Idempotent — entries with status="promoted" are skipped on subsequent
    runs.

    Returns the number of state entries this pass changed — promotions plus
    the origin stamps it back-fills onto entries that predate
    ``StateEntry.origin_backfill``. Non-zero means the caller must persist the
    state file; counting the stamps too is what makes the healed origin
    survive the run it was recovered in.
    """
    from mnemo.core.extract.inbox.branches.universal_promotion import (
        _apply_universal_promotion,
        _universal_threshold,
    )
    from mnemo.core.extract.inbox.paths import _inbox_path
    from mnemo.core.extract.inbox.rendering import _extract_body
    from mnemo.core.extract.inbox.types import ApplyResult
    from mnemo.core.extract.scanner import parse_frontmatter
    from mnemo.core.rule_activation.index import is_universal, projects_for_rule

    threshold = _universal_threshold()
    changed_count = 0
    # Snapshot keys: handler mutates state.entries (status flips, key reused).
    keys = [
        key for key, entry in state.entries.items()
        if entry.status == "inbox"
        and is_universal(projects_for_rule(entry.source_files), threshold)
    ]
    for key in keys:
        entry = state.entries.get(key)
        if entry is None:
            continue
        try:
            page_type, slug = key.split("/", 1)
        except ValueError:
            continue
        inbox_md = vault_root / "shared" / "_inbox" / page_type / f"{slug}.md"
        if not inbox_md.exists():
            # State entry references an _inbox file the user deleted —
            # nothing to promote. Leave the entry alone; doctor surfaces it.
            continue
        try:
            text = inbox_md.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body = parse_frontmatter(text)
        if str(fm.get("demoted_from") or "") == "feedback":
            # Demoted feedback stays staged until a person reviews it.
            continue
        page = inbox.ExtractedPage(
            slug=slug,
            type=page_type,
            name=str(fm.get("name") or slug),
            description=str(fm.get("description") or ""),
            body=_extract_body(text),
            source_files=list(entry.source_files),
            source_hash=entry.source_hash,
            stability=str(fm.get("stability") or "stable"),
            tags=list(fm.get("tags") or []),
            # Two durable readings, OR'd: the stamp written top-level by
            # rendering._render_page (the shared predicate also accepts the
            # nested spelling, so a hand-edited or future-rendered page cannot
            # slip past), and the sticky flag on the state entry — which still
            # answers True if someone stripped the stamp out of the staged
            # file by hand.
            origin_backfill=(
                is_backfill_frontmatter(fm) or is_backfill_entry(entry)
            ),
        )
        if page.origin_backfill:
            # Reconstructed from archived transcripts — the origin gate keeps
            # it staged until a human reviews it, cross-project or not. Stamp
            # the entry on the way past so a vault whose state predates the
            # field carries the answer from here on, even if the user later
            # edits the stamp out of the staged file.
            if not entry.origin_backfill:
                entry.origin_backfill = True
                changed_count += 1
            continue
        scratch = ApplyResult()
        target = _inbox_path(vault_root, page)
        try:
            _apply_universal_promotion(
                page, entry, target, vault_root, state,
                run_id=run_id, force=False, result=scratch,
            )
        except Exception as exc:  # noqa: BLE001 — reconciler must fail-open
            errors.log_error(vault_root, "extract.reconcile_universal", exc)
            continue
        if scratch.universal_promoted:
            changed_count += 1
        _merge_apply(scratch, summary)
    return changed_count


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
    only: str | None = None,
) -> ExtractionSummary:
    """Consolidate the vault's dirty memory files into rule pages.

    ``only`` narrows the consolidation to a single scanner key
    (``"<type>/<slug>"``, e.g. ``"feedback/briefing-<session-id>"``) and skips
    the project-promotion phase. Index rebuilds stay vault-wide either way —
    they are cheap, local, and a partial index is worse than none.
    """
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
                run_id=run_id, dry_run=dry_run, force=force, only=only,
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
            # Rebuild the rule-activation index so the PreToolUse hook sees
            # any newly-emitted enforce / activates_on blocks on the next
            # tool call. Never raises — extraction always moves on.
            try:
                from mnemo.core import rule_activation
                rule_activation.write_index(
                    vault_root, rule_activation.build_index(vault_root)
                )
            except Exception as exc:  # noqa: BLE001 — fail-open by design
                errors.log_error(vault_root, "extract.rule_activation_index", exc)
            # Rebuild the reflex BM25F index as well. Newly-promoted rules
            # would otherwise be invisible to UserPromptSubmit retrieval
            # until the next SessionStart triggers a rebuild.
            try:
                from mnemo.core.reflex import index as reflex_index
                reflex_index.write_index(
                    vault_root, reflex_index.build_index(vault_root)
                )
            except Exception as exc:  # noqa: BLE001 — fail-open by design
                errors.log_error(vault_root, "extract.reflex_index", exc)

        summary.wall_time_s = time.monotonic() - start

        # A scoped run is not an auto run: `last-auto-run.json` feeds
        # `mnemo status`'s "last run" line, which reports the vault-wide
        # sweep, not a one-file `mnemo learn`.
        if background and not dry_run and only is None:
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
