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

from mnemo.core.extract.inbox_paths import (
    _inbox_path,
    _is_auto_promoted_target,
    _promoted_path,
    _sibling_path,
    _target_path_for_page,
)
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
    stability: str = "stable"
    tags: list[str] = field(default_factory=list)
    # Optional activation metadata (Task 5). Both are serialized into the
    # page frontmatter when non-None and consumed by rule_activation.build_index
    # on the next extraction run.
    enforce: dict | None = None
    activates_on: dict | None = None


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


_YAML_SPECIALS = (":", "#", '"', "'", "{", "}", "[", "]", ",", "&", "*", "!", "|", ">", "%", "@", "`")


def _yaml_scalar(value: object) -> str:
    """Serialize *value* as a minimal YAML-safe scalar.

    Quotes only when the string contains YAML-special characters or leading/
    trailing whitespace. Uses single-quoted form with doubled inner quotes —
    the cheapest form that round-trips cleanly through the parse_frontmatter
    extension from Task 1 (which strips a matched pair of surrounding
    single/double quotes via ``_dequote``).
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    s = str(value)
    if s == "":
        return "''"
    needs_quoting = (
        s != s.strip()
        or any(c in s for c in _YAML_SPECIALS)
    )
    if not needs_quoting:
        return s
    escaped = s.replace("'", "''")
    return f"'{escaped}'"


def _yaml_inline_list(items: list) -> str:
    return "[" + ", ".join(_yaml_scalar(i) for i in items) + "]"


def _render_nested_block(key: str, data: dict) -> str:
    """Render a top-level key whose value is a dict of scalars / lists.

    Writes::

        key:
          subkey: scalar
          subkey2: [a, b, c]
          subkey3:
            - long item one
            - long item two

    Conforms to the Task 1 parse_frontmatter extension — 2-space indent for
    subkeys, 4-space indent for sub-block-list items.
    """
    lines = [f"{key}:"]
    for subkey, subval in data.items():
        if isinstance(subval, list):
            # Inline short, block long. path_globs always goes block.
            if subkey == "path_globs":
                use_inline = False
            else:
                total_len = sum(len(str(i)) for i in subval) + 2 * len(subval)
                use_inline = len(subval) <= 4 and total_len < 60
            if use_inline:
                lines.append(f"  {subkey}: {_yaml_inline_list(subval)}")
            else:
                lines.append(f"  {subkey}:")
                for item in subval:
                    lines.append(f"    - {_yaml_scalar(item)}")
        else:
            lines.append(f"  {subkey}: {_yaml_scalar(subval)}")
    return "\n".join(lines) + "\n"


def _render_page(page: ExtractedPage, *, run_id: str, auto_promoted: bool = False) -> str:
    sources_yaml = "\n".join(f"  - {s}" for s in page.source_files)
    if auto_promoted:
        extras = f"last_sync: {run_id}\n"
        system_marker = "auto-promoted"
    else:
        extras = ""
        system_marker = "needs-review"
    stability = getattr(page, "stability", None) or "stable"
    # Unified tags list: system marker first, then LLM-emitted topic tags.
    # The shared filter (core/filters.py) reads this same list; topic_tags()
    # strips the marker when bucketing by topic in the HOME dashboard.
    page_tags = list(getattr(page, "tags", None) or [])
    all_tags = [system_marker]
    for t in page_tags:
        if t and t != system_marker and t not in all_tags:
            all_tags.append(t)
    tags_yaml = "\n".join(f"  - {t}" for t in all_tags)
    # Optional activation blocks — only written when non-empty. Both flow
    # through _render_nested_block so indentation is guaranteed to match
    # what parse_frontmatter (Task 1 extension) expects.
    enforce_block = ""
    if isinstance(page.enforce, dict) and page.enforce:
        enforce_block = _render_nested_block("enforce", page.enforce)
    activates_on_block = ""
    if isinstance(page.activates_on, dict) and page.activates_on:
        activates_on_block = _render_nested_block("activates_on", page.activates_on)
    return (
        "---\n"
        f"name: {_yaml_scalar(page.name)}\n"
        f"description: {_yaml_scalar(page.description)}\n"
        f"type: {page.type}\n"
        f"extracted_at: {run_id}\n"
        f"extraction_run: {run_id}\n"
        f"stability: {stability}\n"
        f"{extras}"
        "sources:\n"
        f"{sources_yaml}\n"
        "tags:\n"
        f"{tags_yaml}\n"
        f"{enforce_block}"
        f"{activates_on_block}"
        "---\n\n"
        f"{page.body}\n"
    )


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
        # Union tags from all merged pages so the LLM's topic vocabulary is
        # preserved. Preserve order: chosen page first, then any extras.
        all_tags: list[str] = []
        for p in [chosen] + [p for p in items if p is not chosen]:
            for t in getattr(p, "tags", None) or []:
                if t not in all_tags:
                    all_tags.append(t)
        merged.append(ExtractedPage(
            slug=chosen.slug,
            type=chosen.type,
            name=chosen.name,
            description=chosen.description,
            body=chosen.body,
            source_files=all_sources,
            source_hash=chosen.source_hash,
            stability=getattr(chosen, "stability", None) or "stable",
            tags=all_tags,
            enforce=getattr(chosen, "enforce", None),
            activates_on=getattr(chosen, "activates_on", None),
        ))
    return merged


def _extract_body(text: str) -> str:
    """Return the markdown body of a mnemo-written page (strip frontmatter)."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    return text[end + len("\n---\n"):]


def _bodies_similar(a: str, b: str, threshold: float = 0.6) -> bool:
    """Cheap Jaccard similarity on lowercase word tokens.

    Used to decide whether a freshly-extracted page is a drifted rewrite of an
    existing page (same underlying rule, new slug) vs. a legitimately distinct
    rule that happens to share a source file.
    """
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return False
    common = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(common) / len(union) >= threshold


_STEM_SUFFIXES = (
    "ations", "ation", "ings", "ing", "ied", "ies", "ers", "ed", "es", "er",
)


def _stem_word(word: str) -> str:
    """Collapse common English inflections to a shared stem.

    Deliberately simple (no Porter stemmer dependency) — just enough to
    fold the dogfood collision between ``populate`` and ``populating`` into
    one canonical form. False merges are caught by the body-similarity
    check in ``_detect_stem_collision``.
    """
    w = word.lower()
    if len(w) < 4:
        return w
    for suf in _STEM_SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 4:
        return w[:-1]
    if w.endswith("e") and len(w) > 4:
        return w[:-1]
    return w


def _stem_slug(slug: str) -> str:
    return "-".join(_stem_word(tok) for tok in slug.split("-") if tok)


def _detect_stem_collision(
    page: ExtractedPage,
    state: ExtractionState,
    vault_root: Path,
) -> str | None:
    """Return an existing slug whose stem matches ``page.slug``, or None.

    Second-layer guardrail that catches inflection drift across runs:
    ``auto-populate-…`` and ``auto-populating-…`` from different source
    sets should collapse to one canonical page. Unlike
    ``_detect_drift_slug`` (which requires identical source files), this
    check relies entirely on slug-stem equality plus body similarity.

    Skips the exact-match case (handled by the normal update flow) and
    stale state entries whose target files no longer exist on disk.
    """
    if not page.slug:
        return None
    candidate_stem = _stem_slug(page.slug)
    if not candidate_stem:
        return None
    for key, entry in state.entries.items():
        if not key.startswith(f"{page.type}/"):
            continue
        existing_slug = key.split("/", 1)[1]
        if existing_slug == page.slug:
            return None  # exact match — update path will handle it
        if _stem_slug(existing_slug) != candidate_stem:
            continue
        existing_target = vault_root / "shared" / page.type / f"{existing_slug}.md"
        if not existing_target.exists():
            existing_target = (
                vault_root / "shared" / "_inbox" / page.type / f"{existing_slug}.md"
            )
            if not existing_target.exists():
                continue
        try:
            existing_text = existing_target.read_text(encoding="utf-8")
        except OSError:
            continue
        existing_body = _extract_body(existing_text)
        if _bodies_similar(page.body, existing_body):
            return existing_slug
    return None


def _detect_drift_slug(
    page: ExtractedPage,
    state: ExtractionState,
    vault_root: Path,
) -> str | None:
    """Return an existing slug this page is a drifted rewrite of, or None.

    Guardrail against LLM non-determinism in slug choice. Triggers when an
    existing state entry for the same ``<type>`` has the EXACT same source
    file set AND a body similar to the new page. Redirects the new page's
    slug to the existing one so ``apply_pages`` treats it as an update rather
    than a fresh write, preventing drift pairs from accumulating.

    Skips stale state entries whose target files no longer exist on disk.
    Handles the legitimate one-source-many-rules case via the body-similarity
    check: distinct rules from the same source file have disjoint tokens and
    fall below the threshold.
    """
    if not page.source_files:
        return None
    source_set = set(page.source_files)
    for key, entry in state.entries.items():
        if not key.startswith(f"{page.type}/"):
            continue
        existing_slug = key.split("/", 1)[1]
        if existing_slug == page.slug:
            return None  # already matching — no drift
        if set(entry.source_files or []) != source_set:
            continue
        # Same source set. Verify existing target file exists (stale state
        # entries are skipped) and compare body content.
        existing_target = vault_root / "shared" / page.type / f"{existing_slug}.md"
        if not existing_target.exists():
            existing_target = (
                vault_root / "shared" / "_inbox" / page.type / f"{existing_slug}.md"
            )
            if not existing_target.exists():
                continue
        try:
            existing_text = existing_target.read_text(encoding="utf-8")
        except OSError:
            continue
        existing_body = _extract_body(existing_text)
        if _bodies_similar(page.body, existing_body):
            return existing_slug
    return None


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

        # Row 1: source_hash unchanged → skip
        if entry is not None and entry.source_hash == page.source_hash and not force:
            result.unchanged_skipped.append(key)
            continue

        # v0.3 upgrade detection: existing auto_promoted entry + multi-source re-emission
        if (
            not is_auto
            and entry is not None
            and entry.status == "auto_promoted"
        ):
            _apply_upgrade_proposed(page, entry, vault_root, run_id, result)
            continue

        if is_auto:
            _apply_auto_promoted(
                page, entry, target, vault_root, state, run_id, force, result,
            )
        else:
            _apply_inbox(
                page, entry, target, vault_root, state, run_id, force, result,
            )

    return result


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
    key = f"{page.type}/{page.slug}"
    content = _render_page(page, run_id=run_id, auto_promoted=True)
    new_written_hash = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()

    if entry is None:
        _atomic_write(target, content)
        state.entries[key] = StateEntry(
            source_files=list(page.source_files),
            source_hash=page.source_hash,
            written_hash=new_written_hash,
            written_at=run_id,
            status="auto_promoted",
            last_sync=run_id,
        )
        result.auto_promoted.append(key)
        return

    if entry.status == "dismissed" and not force:
        result.dismissed_skipped.append(key)
        return

    # v0.2 → v0.3 migration: legacy status=inbox + target is now sacred
    if entry.status == "inbox" and not target.exists():
        _atomic_write(target, content)
        entry.status = "auto_promoted"
        entry.source_files = list(page.source_files)
        entry.source_hash = page.source_hash
        entry.written_hash = new_written_hash
        entry.written_at = run_id
        entry.last_sync = run_id
        result.auto_promoted.append(key)
        return

    if not target.exists():
        # status was auto_promoted but user deleted the sacred file → dismissed
        if force:
            _atomic_write(target, content)
            entry.status = "auto_promoted"
            entry.source_files = list(page.source_files)
            entry.source_hash = page.source_hash
            entry.written_hash = new_written_hash
            entry.written_at = run_id
            entry.last_sync = run_id
            result.auto_promoted.append(key)
        else:
            entry.status = "dismissed"
            result.dismissed_skipped.append(key)
        return

    # target exists — check if user edited it
    disk_hash = _file_hash(target)
    if disk_hash == entry.written_hash:
        _atomic_write(target, content)
        entry.source_files = list(page.source_files)
        entry.source_hash = page.source_hash
        entry.written_hash = new_written_hash
        entry.written_at = run_id
        entry.last_sync = run_id
        entry.status = "auto_promoted"
        result.overwrite_safe.append(key)
    else:
        # user edited the sacred file — bounce sibling back into _inbox/
        sibling = _sibling_path(target, vault_root)
        _atomic_write(sibling, content)
        result.sibling_bounced.append((key, str(sibling)))


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
    key = f"{page.type}/{page.slug}"
    content = _render_page(page, run_id=run_id, auto_promoted=False)
    new_written_hash = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    promoted_file = _promoted_path(vault_root, page)

    if entry is None:
        _atomic_write(target, content)
        state.entries[key] = StateEntry(
            source_files=list(page.source_files),
            source_hash=page.source_hash,
            written_hash=new_written_hash,
            written_at=run_id,
            status="inbox",
            last_sync=run_id,
        )
        result.written_fresh.append(key)
        return

    status = entry.status

    if status == "dismissed":
        if force:
            _atomic_write(target, content)
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
        return

    if status == "promoted":
        if promoted_file.exists():
            update_file = target.with_name(f"{page.slug}.update-proposed.md")
            _atomic_write(update_file, content)
            entry.source_hash = page.source_hash
            entry.source_files = list(page.source_files)
            result.update_proposed.append(key)
        else:
            entry.status = "dismissed"
            result.dismissed_skipped.append(key)
        return

    # status == "inbox" (the v0.2 main case)
    if target.exists():
        disk_hash = _file_hash(target)
        if disk_hash == entry.written_hash:
            _atomic_write(target, content)
            entry.source_files = list(page.source_files)
            entry.source_hash = page.source_hash
            entry.written_hash = new_written_hash
            entry.written_at = run_id
            entry.last_sync = run_id
            result.overwrite_safe.append(key)
        else:
            sibling = _sibling_path(target, vault_root)
            _atomic_write(sibling, content)
            result.sibling_proposed.append((key, str(sibling)))
    else:
        if promoted_file.exists():
            entry.status = "promoted"
            update_file = target.with_name(f"{page.slug}.update-proposed.md")
            _atomic_write(update_file, content)
            entry.source_hash = page.source_hash
            entry.source_files = list(page.source_files)
            result.update_proposed.append(key)
        elif force:
            _atomic_write(target, content)
            entry.source_files = list(page.source_files)
            entry.source_hash = page.source_hash
            entry.written_hash = new_written_hash
            entry.written_at = run_id
            entry.last_sync = run_id
            entry.status = "inbox"
            result.written_fresh.append(key)
        else:
            entry.status = "dismissed"
            result.dismissed_skipped.append(key)


def _apply_upgrade_proposed(
    page: ExtractedPage,
    entry: StateEntry,
    vault_root: Path,
    run_id: str,
    result: ApplyResult,
) -> None:
    """Multi-source re-emission of a slug that was already auto_promoted.

    Writes a sibling proposal into _inbox/ WITHOUT touching the existing
    auto_promoted state entry or the sacred file.  The user decides whether
    to merge the upgrade by hand.
    """
    key = f"{page.type}/{page.slug}"
    content = _render_page(page, run_id=run_id, auto_promoted=False)
    sibling = (
        vault_root
        / "shared"
        / "_inbox"
        / page.type
        / f"{page.slug}.proposed.md"
    )
    _atomic_write(sibling, content)
    result.upgrade_proposed.append((key, str(sibling)))


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
