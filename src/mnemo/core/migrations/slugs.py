"""#114: stamp ``slug:`` into legacy rule pages.

Every page written by ``_render_page`` before this migration carried ``name:``
(display) but no ``slug:``, while the file itself was always written under the
normalized LLM slug. ``derive_rule_slug`` (slug → name → stem) therefore keyed
the reflex/activation indexes, ``disable-rule`` and the MCP tools by the
display name, and the learned ledger by the slug. Writing the slug down makes
the priority chain pick the same identifier everywhere. Idempotent; the
caller rebuilds both indexes in the same step.

The stamped value is ``_normalize_slug(stem)`` for feedback/reference/user
pages (normalisation is the identity for every stem those types write) but
the stem *verbatim* for ``shared/project/`` and ``shared/_inbox/project/``:
those stems are the composite ``<agent>__<slug>`` that ``promote._project_slug``
builds and that ``_learned_entries_for_projects`` records in the ledger as the
rule's ``slug``. Normalising would turn ``__`` into ``-`` and recreate the
ledger-vs-index mismatch this migration exists to close (225 pages on the
real vault, 2026-09-02).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mnemo.core.atomic import atomic_write_bytes
from mnemo.core.extract.scanner import _normalize_slug
from mnemo.core.filters import iter_shared_pages, parse_frontmatter

# Written after a run that found nothing left to stamp, so steady-state
# session starts skip the frontmatter scan entirely.
MARKER_REL = ".mnemo/slugs-stamped.v1"


@dataclass
class SlugReport:
    scanned: int = 0
    stamped: int = 0
    #: Entries whose ``written_hash`` this run advanced to match bytes the
    #: migration itself wrote (#179). Counts both pages stamped by this run
    #: and pages an earlier, pre-fix run stamped and left drifted.
    reconciled: int = 0
    skipped: list[tuple[Path, str]] = field(default_factory=list)


def _fm_span(text: str) -> Optional[tuple[int, int]]:
    """(start of first key line, index of the closing ``---`` line).

    Same shape as ``reclassify_apply._fm_span``; duplicated rather than
    imported so this module stays free of the reclassify dependency chain.
    """
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    return 4, end + 1


def _stamp(text: str, slug: str) -> str:
    """Insert ``slug: <slug>`` after the first ``name:`` line, or as the first
    frontmatter line when there is no ``name:``. Every other byte survives."""
    span = _fm_span(text)
    assert span is not None  # callers check first
    start, close = span
    lines = text[start:close].splitlines()
    out: list[str] = []
    done = False
    for line in lines:
        out.append(line)
        if not done and line.startswith("name:"):
            out.append(f"slug: {slug}")
            done = True
    if not done:
        out.insert(0, f"slug: {slug}")
    return text[:start] + "\n".join(out) + "\n" + text[close:]


def _unstamp(text: str) -> Optional[str]:
    """Return *text* with the first frontmatter ``slug:`` line removed.

    The exact inverse of :func:`_stamp`, which inserts one line and leaves
    every other byte alone. Used by :func:`_reconcile` to ask "were these
    bytes produced by this migration, or by a person?" — if hashing the
    unstamped text reproduces the recorded ``written_hash``, the only change
    since that write was the stamp, and the entry can be advanced safely.

    None when there is no frontmatter or no ``slug:`` line to remove.
    """
    span = _fm_span(text)
    if span is None:
        return None
    start, close = span
    lines = text[start:close].splitlines()
    for i, line in enumerate(lines):
        if line.startswith("slug:"):
            rest = lines[:i] + lines[i + 1:]
            body = ("\n".join(rest) + "\n") if rest else ""
            return text[:start] + body + text[close:]
    return None


_PROJECT_TYPE = "project"


def _slug_for(md: Path, shared: Path) -> str:
    """Canonical slug for the page at ``md``: composite stem verbatim for
    project pages, normalized stem for everything else (see module doc)."""
    parts = md.relative_to(shared).parts[:-1]
    if parts and parts[0] == "_inbox":
        parts = parts[1:]
    if parts and parts[0] == _PROJECT_TYPE:
        return md.stem
    return _normalize_slug(md.stem)


_STATE_REL = ".mnemo/extraction-state.json"


def _key_for(md: Path, shared: Path) -> Optional[str]:
    """``<type>/<slug>`` for a live page, or None when it is not one.

    Mirrors the key shape ``apply_pages`` and ``promote_projects`` install:
    the type directory and the page's slug, with the ``_inbox`` hop dropped
    because staged and promoted pages share one entry.
    """
    parts = md.relative_to(shared).parts[:-1]
    if parts and parts[0] == "_inbox":
        parts = parts[1:]
    if not parts:
        return None
    return f"{parts[0]}/{_slug_for(md, shared)}"


def _reconcile(vault_root: Path, touched: dict[str, Path]) -> int:
    """Advance ``written_hash`` for entries whose page this migration wrote.

    A bulk rewriter owns the bytes it produces, so leaving ``written_hash``
    behind makes every page it touched read as "user edited" to
    ``extract/inbox/branches/*`` and ``promote.py`` — the condition that
    stages a ``.proposed.md`` instead of updating the page in place (#179).
    ``reclassify_apply.py`` already does this for its own writes.

    Two populations are healed, because on every real vault the migration has
    already run once without this step:

    * pages *this* run stamped — ``touched`` carries them;
    * pages an earlier run stamped and left drifted — found by walking the
      state and testing each entry.

    The safety property is that a genuine hand edit must NOT be reconciled,
    or ``written_hash`` stops meaning anything. So an entry is advanced only
    when :func:`_unstamp` of the page on disk hashes back to the recorded
    ``written_hash``: i.e. the one and only difference between what the state
    remembers and what is on disk is the stamp this migration adds. Anything
    else — a reworded body, a new tag — fails that test and is left alone for
    a person to resolve.
    """
    from mnemo.core.extract.inbox.io import content_hash
    from mnemo.core.extract.inbox.state_io import atomic_write_state, load_state

    state_path = Path(vault_root) / _STATE_REL
    if not state_path.is_file():
        return 0
    try:
        state = load_state(state_path)
    except Exception:  # noqa: BLE001 — a broken state file is not this migration's to fix
        return 0

    shared = Path(vault_root) / "shared"
    healed = 0
    for key, entry in state.entries.items():
        page = touched.get(key)
        if page is None:
            ty, _, slug = key.partition("/")
            for cand in (shared / ty / f"{slug}.md",
                         shared / "_inbox" / ty / f"{slug}.md"):
                if cand.is_file():
                    page = cand
                    break
        if page is None or not page.is_file():
            continue
        try:
            text = page.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if content_hash(text) == entry.written_hash:
            continue  # already in sync
        without = _unstamp(text)
        if without is None or content_hash(without) != entry.written_hash:
            continue  # a real edit, or drift this migration did not cause
        entry.written_hash = content_hash(text)
        healed += 1

    if healed:
        atomic_write_state(state, state_path)
    return healed


def stamp_slugs(vault_root: Path, *, dry_run: bool = False) -> SlugReport:
    """Stamp every live page (``_inbox`` included, ``_archive`` excluded) that
    lacks a non-empty string ``slug``. The slug is the file stem — normalized
    for cluster pages, verbatim for project pages — which is what every page
    was written under in the first place."""
    rep = SlugReport()
    shared = Path(vault_root) / "shared"
    touched: dict[str, Path] = {}
    for md in iter_shared_pages(Path(vault_root), include_inbox=True):
        rep.scanned += 1
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            rep.skipped.append((md, f"read: {exc}"))
            continue
        if _fm_span(text) is None:
            rep.skipped.append((md, "no frontmatter"))
            continue
        existing = parse_frontmatter(text).get("slug")
        if isinstance(existing, str) and existing.strip():
            continue
        new = _stamp(text, _slug_for(md, shared))
        rep.stamped += 1
        if not dry_run:
            atomic_write_bytes(md, new.encode("utf-8"))
            key = _key_for(md, shared)
            if key is not None:
                touched[key] = md
    # #179: the bytes above are ours, so the state must be told. Runs even when
    # nothing was stamped: every real vault already has the marker on disk, so
    # its drift is historical and would otherwise never heal.
    if not dry_run:
        rep.reconciled = _reconcile(Path(vault_root), touched)
    return rep


def marker_present(vault_root: Path) -> bool:
    """True once a run stamped every page; session start then skips the scan."""
    return (Path(vault_root) / MARKER_REL).exists()


def write_marker(vault_root: Path) -> None:
    """Record a complete migration. Only written when nothing was skipped, so a
    page that could not be parsed keeps the scan alive until it is fixed."""
    m = Path(vault_root) / MARKER_REL
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text("1\n", encoding="utf-8")
