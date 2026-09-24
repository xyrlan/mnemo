"""Executing a reclassify plan: frontmatter surgery, apply, and byte-exact undo.

Split out of :mod:`mnemo.core.reclassify` (which stayed the planning half) to
keep each module readable. :func:`apply` never calls an LLM — everything it
needs was decided and validated when the plan was built — and every file it
touches is copied to ``originals/`` first so :func:`undo` is a pure byte
restore rather than an inverse transformation.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from mnemo.core import errors
from mnemo.core.extract.inbox.io import atomic_write, content_hash
from mnemo.core.extract.machine_edits import edit_session, page_key
from mnemo.core.extract.scanner import ExtractionState, StateEntry
from mnemo.core.extract.inbox.rendering import _render_nested_block
from mnemo.core.reclassify_types import ApplyReport, Plan, split_frontmatter


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------- frontmatter surgery


def _fm_span(text: str) -> Optional[tuple[int, int]]:
    """(start of first key line, index of the closing ``---`` line)."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    return 4, end + 1


def _rewrite_keep(
    text: str, quote: Optional[str], source: Optional[str], link: Optional[str] = None,
) -> str:
    span = _fm_span(text)
    if span is None:
        return text
    start, close = span
    lines = text[start:close].splitlines()
    out: list[str] = []
    inserted = False
    # A page reclassify itself kept once already carries ``evidence:``; a
    # second keep (``mnemo reverify``, #257) must replace that block, not
    # leave two top-level keys for the parser to pick between.
    in_evidence = False
    for line in lines:
        if in_evidence and (line.startswith("  ") or not line.strip()):
            continue
        in_evidence = False
        if line.startswith("evidence:"):
            in_evidence = True
            continue
        if not inserted and line.startswith("confidence:"):
            out.append("confidence: verified")
            inserted = True
            continue
        if not inserted and line.startswith("stability:"):
            out.append(line)
            out.append("confidence: verified")
            inserted = True
            continue
        if not inserted and line.startswith("sources:"):
            out.append("confidence: verified")
            out.append(line)
            inserted = True
            continue
        out.append(line)
    if not inserted:
        out.append("confidence: verified")
    evidence = _render_nested_block(
        "evidence", {"quote": quote or "", "source": source or "", "link": link or ""},
    )
    out.append(evidence.rstrip("\n"))
    return text[:start] + "\n".join(out) + "\n" + text[close:]


def _rewrite_demote(text: str) -> str:
    span = _fm_span(text)
    if span is None:
        return text
    start, close = span
    lines = text[start:close].splitlines()
    out: list[str] = []
    done = False
    for line in lines:
        # The page's own ``confidence:`` / ``demoted_from:`` would otherwise
        # follow the ones written here, and the parser keeps the last key it
        # sees — a demoted page reading ``confidence: verified`` (#257).
        if line.startswith("confidence:") or line.startswith("demoted_from:"):
            continue
        if not done and line.startswith("type:"):
            out.append("type: reference")
            out.append("confidence: inferred")
            out.append("demoted_from: feedback")
            done = True
            continue
        out.append(line)
    if not done:
        out = ["type: reference", "confidence: inferred", "demoted_from: feedback"] + out
    return text[:start] + "\n".join(out) + "\n" + text[close:]


def _append_sources(text: str, new_sources: list) -> str:
    """Add ``  - <src>`` lines the target's ``sources:`` block lacks."""
    span = _fm_span(text)
    if span is None or not new_sources:
        return text
    start, close = span
    lines = text[start:close].splitlines()
    try:
        idx = next(i for i, l in enumerate(lines) if l.startswith("sources:"))
    except StopIteration:
        anchor = next((i for i, l in enumerate(lines) if l.startswith("tags:")), len(lines))
        block = ["sources:"] + [f"  - {s}" for s in new_sources]
        lines = lines[:anchor] + block + lines[anchor:]
        return text[:start] + "\n".join(lines) + "\n" + text[close:]
    end = idx + 1
    while end < len(lines) and lines[end].startswith("  - "):
        end += 1
    have = {l[4:].strip().strip("'\"") for l in lines[idx + 1:end]}
    additions = [f"  - {s}" for s in new_sources if str(s) not in have]
    if not additions:
        return text
    lines = lines[:end] + additions + lines[end:]
    return text[:start] + "\n".join(lines) + "\n" + text[close:]


# -------------------------------------------------------------------- apply


def _entry_for(state: ExtractionState, key: str, sources: list) -> StateEntry:
    entry = state.entries.get(key)
    if entry is None:
        now = _now()
        entry = StateEntry(
            source_files=list(sources), source_hash="", written_hash="",
            written_at=now, status="auto_promoted", last_sync=now,
        )
        state.entries[key] = entry
    return entry


def _key(path: Path, vault_root: Path) -> str:
    """The ledger key of the page at *path*: its type directory and its file
    stem, as extraction installs it — never the frontmatter slug a verdict
    names. The two differ for most rules of a real vault (61/61 keeps of the
    09-02 run), and an entry keyed on the slug is an orphan: the page's real
    entry never moves, so the page reads as edited by a person (#492)."""
    return page_key(path, vault_root) or f"{path.parent.name}/{path.stem}"


def _slug_to_path(vault_root: Path) -> dict:
    """slug -> rule file, built the same way ``plan`` derived the slugs.

    ``collect_rules`` takes the slug from frontmatter (``derive_rule_slug``), so
    on the real vault the filename and the slug disagree for ~97% of rules. This
    map is the fallback for plans written before verdicts carried a ``path``;
    a path is NEVER reconstructed as ``shared/feedback/{slug}.md``.
    """
    from mnemo.core.reclassify import collect_rules

    return {r.slug: r.path for r in collect_rules(vault_root)}


def apply(
    vault_root: Path, plan_obj: Plan, *, rebuild_indexes: bool = True, session=None,
) -> ApplyReport:
    """Execute *plan_obj*, keeping byte-exact originals for :func:`undo`.

    Runs under the extraction lock (:func:`machine_edits.edit_session`, which
    raises ``VaultBusy`` while an extraction holds it). A kept page and a
    merge target are written through the session, so their ``written_hash``
    moves only when the page was the extractor's bytes before: a page a
    person had edited keeps reading as edited.

    A caller that already holds a session (``reverify`` swaps briefings first,
    under the same lock) passes it as *session*.
    """
    vault_root = Path(vault_root)
    if session is not None:
        report = _apply(vault_root, plan_obj, session)
        session.changed = True
    else:
        with edit_session(vault_root, create=True) as own:
            report = _apply(vault_root, plan_obj, own)
            own.changed = True

    if rebuild_indexes:
        try:
            from mnemo.core import rule_activation
            rule_activation.write_index(vault_root, rule_activation.build_index(vault_root))
        except Exception as exc:  # noqa: BLE001 — index rebuild is best-effort
            errors.log_error(vault_root, "reclassify.rule_activation_index", exc)
        try:
            from mnemo.core.reflex import index as reflex_index
            reflex_index.write_index(vault_root, reflex_index.build_index(vault_root))
        except Exception as exc:  # noqa: BLE001
            errors.log_error(vault_root, "reclassify.reflex_index", exc)

    return report


def _apply(vault_root: Path, plan_obj: Plan, session) -> ApplyReport:
    state_path = vault_root / ".mnemo" / "extraction-state.json"
    state = session.state
    if state is None:
        # A state this mnemo cannot read (a newer schema) is not ours to
        # rewrite; nothing has been touched yet.
        raise RuntimeError(f"{state_path} is not readable by this mnemo; not applying")
    arch = vault_root / "shared" / "_archive" / f"reclassify-{plan_obj.run_id}"
    # Applying the same run twice would re-copy already-modified files over the
    # pristine originals and overwrite the manifest, silently destroying undo.
    if (arch / "manifest.json").exists():
        raise RuntimeError(f"run {plan_obj.run_id} already applied; undo it first")
    originals = arch / "originals"
    merged_dir = arch / "merged"
    archived_dir = arch / "archived"
    for d in (originals, merged_dir, archived_dir):
        d.mkdir(parents=True, exist_ok=True)

    state_backup: Optional[str] = None
    if state_path.exists():
        backup = originals / "extraction-state.json"
        shutil.copy2(state_path, backup)
        state_backup = str(backup.relative_to(vault_root))

    report = ApplyReport(archive_dir=arch)
    moves: list[dict] = []
    skipped: list[dict] = []
    # Built lazily: only plans that predate ``Verdict.path`` need it, and
    # collect_rules re-reads every rule file.
    fallback_map: Optional[dict] = None

    def _resolve(slug: Optional[str], rel: Optional[str]) -> Optional[Path]:
        nonlocal fallback_map
        if rel:
            candidate = vault_root / rel
            if candidate.exists():
                return candidate
        if not slug:
            return None
        if fallback_map is None:
            fallback_map = _slug_to_path(vault_root)
        candidate = fallback_map.get(slug)
        return candidate if candidate is not None and candidate.exists() else None
    # Every slug whose pristine bytes already sit in originals/. A slug can be
    # reached twice — once as its own verdict, once as another rule's merge
    # target — and the second copy must never overwrite the first, or undo
    # would restore an already-modified file.
    backed_up: set = set()

    def _back_up(path: Path, slug: str) -> Path:
        dest = originals / f"{slug}.md"
        if slug not in backed_up:
            shutil.copy2(path, dest)
            backed_up.add(slug)
        return dest

    def _rel(p: Path) -> str:
        # Manifest paths are POSIX so a manifest written on Windows restores on
        # any OS (and vice versa); Path() accepts "/" everywhere when reading.
        try:
            return p.relative_to(vault_root).as_posix()
        except ValueError:
            return p.as_posix()

    for v in plan_obj.verdicts:
        resolved = _resolve(v.slug, getattr(v, "path", None))
        if resolved is None:
            skipped.append({"slug": v.slug, "reason": "rule file not found"})
            continue
        src_path = resolved
        text = src_path.read_text(encoding="utf-8")
        fm, _body = split_frontmatter(text)
        fm_sources = fm.get("sources") or []
        if not isinstance(fm_sources, list):
            fm_sources = [fm_sources]
        fm_sources = [str(s) for s in fm_sources]

        _back_up(src_path, v.slug)

        verdict = v.verdict
        target_original: Optional[str] = None
        target_rel: Optional[str] = None
        to_path: Optional[Path] = None

        target_path: Optional[Path] = None
        if verdict == "merge":
            target_path = (_resolve(v.target, getattr(v, "target_path", None))
                           if v.target else None)
            if target_path is None:
                report.notes.append(f"{v.slug}: merge target missing → demoted")
                verdict = "demote"

        src_key = _key(src_path, vault_root)
        if verdict == "keep":
            session.write(src_path, _rewrite_keep(text, v.quote, v.source, v.link))
            if src_key not in state.entries:
                # No entry to compare against: record the kept bytes as the
                # extractor's, as keep always has.
                _entry_for(state, src_key, fm_sources).written_hash = content_hash(src_path)
            to_path = src_path
            report.kept += 1

        elif verdict == "demote":
            dest = vault_root / "shared" / "reference" / f"{v.slug}.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                clash = originals / f"reference__{v.slug}.md"
                shutil.copy2(dest, clash)
            atomic_write(dest, _rewrite_demote(text))
            src_path.unlink()
            old = state.entries.pop(src_key, None)
            now = _now()
            state.entries[_key(dest, vault_root)] = StateEntry(
                source_files=fm_sources,
                source_hash=old.source_hash if old is not None else "",
                written_hash=content_hash(dest),
                written_at=now,
                status="auto_promoted",
                last_sync=now,
                # Sticky: a rule reconstructed from an archived transcript stays
                # behind the origin gate after it is demoted to reference.
                origin_backfill=old.origin_backfill if old is not None else False,
            )
            to_path = dest
            report.demoted += 1

        elif verdict == "merge":
            assert target_path is not None  # resolved in the pre-check above
            target_original = _rel(_back_up(target_path, v.target))
            # The target's *real* vault-relative path, not a path rebuilt from
            # its slug: 97% of the real vault has filename != slug, so undo
            # must restore to where the file actually lives.
            target_rel = _rel(target_path)
            session.write(
                target_path,
                _append_sources(target_path.read_text(encoding="utf-8"), fm_sources),
            )
            dest = merged_dir / f"{v.slug}.md"
            shutil.move(str(src_path), str(dest))
            # Keyed by the page's own type, not always ``feedback/``: an
            # extraction-state key that names a type the page never had leaves
            # the real entry live, and the next ``mnemo extract`` writes the
            # merged-away page back. ``reclassify`` only ever grades
            # ``shared/feedback/``, so for it both lines read as they did.
            _entry_for(state, src_key, fm_sources).status = "dismissed"
            tgt_entry = _entry_for(state, _key(target_path, vault_root), [])
            existing = list(tgt_entry.source_files)
            tgt_entry.source_files = existing + [s for s in fm_sources if s not in existing]
            to_path = dest
            report.merged += 1

        else:  # archive
            dest = archived_dir / f"{v.slug}.md"
            shutil.move(str(src_path), str(dest))
            # Keyed by the page's own type, as for merge: a bulk archive of
            # ``reference`` pages must dismiss ``reference/<slug>``, or the
            # real entry stays live and the next extraction writes it back.
            _entry_for(state, src_key, fm_sources).status = "dismissed"
            to_path = dest
            report.archived += 1

        moves.append({
            "slug": v.slug,
            "verdict": verdict,
            "from": _rel(src_path),
            "to": _rel(to_path) if to_path is not None else None,
            "target": v.target if verdict == "merge" else None,
            "target_original": target_original,
            "target_path": target_rel,
        })

    report.skipped = skipped
    (arch / "manifest.json").write_text(json.dumps({
        "run_id": plan_obj.run_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "moves": moves,
        "skipped": skipped,
        "state_backup": state_backup,
    }, indent=2), encoding="utf-8")
    return report


def undo(vault_root: Path, run_id: str) -> int:
    """Restore every file *run_id* touched, byte for byte. Returns files restored."""
    vault_root = Path(vault_root)
    arch = vault_root / "shared" / "_archive" / f"reclassify-{run_id}"
    manifest_path = arch / "manifest.json"
    if not manifest_path.exists():
        return 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    originals = arch / "originals"
    restored = 0

    for move in manifest.get("moves") or []:
        slug = move.get("slug")
        verdict = move.get("verdict")
        src = originals / f"{slug}.md"
        dest = vault_root / str(move.get("from") or "")
        if src.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
            restored += 1
        if verdict == "demote":
            to = move.get("to")
            if to:
                demoted = vault_root / str(to)
                clash = originals / f"reference__{slug}.md"
                if clash.exists():
                    demoted.write_bytes(clash.read_bytes())
                    restored += 1
                elif demoted.exists():
                    demoted.unlink()
        elif verdict == "merge":
            target_original = move.get("target_original")
            if target_original:
                backup = vault_root / str(target_original)
                # Manifests written before target_path existed only knew the
                # slug; fall back to the old (often wrong) reconstruction so
                # an old run stays as undoable as it ever was.
                target_rel = move.get("target_path")
                target = (
                    vault_root / str(target_rel) if target_rel
                    else vault_root / "shared" / "feedback" / f"{move.get('target')}.md"
                )
                if backup.exists():
                    target.write_bytes(backup.read_bytes())
                    restored += 1

    backup_rel = manifest.get("state_backup")
    if backup_rel:
        backup = vault_root / str(backup_rel)
        if backup.exists():
            state_path = vault_root / ".mnemo" / "extraction-state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_bytes(backup.read_bytes())
            restored += 1

    return restored
