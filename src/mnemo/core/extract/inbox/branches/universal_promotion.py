"""Apply branch for pages whose cross-project source count crosses
``scoping.universalThreshold`` (default 2).

Routes a page that would otherwise live in ``shared/_inbox/<type>/<slug>.md``
directly into the sacred dir ``shared/<type>/<slug>.md`` and removes the
staging copy. Status flips to ``"promoted"`` (the existing status value
already understood by :mod:`branches.inbox_flow._handle_promoted`).

The handler reuses :func:`union_with_prior_sources` so re-extracting the
same slug from a fresh project adds to the project list rather than
overwriting it; the union is what makes a previously-inbox entry cross
the threshold on its second extraction.

Discovered by the v0.15 dogfood: six rules sat in ``_inbox/feedback/`` with
``source_files`` already spanning two projects but with no dispatch branch
that would move them out. See ``docs/superpowers/plans/joyful-cooking-wirth.md``.
"""
from __future__ import annotations

import functools
from pathlib import Path

from mnemo.core.extract.inbox.io import atomic_write, content_hash
from mnemo.core.extract.inbox.paths import _inbox_path, _promoted_path, _sibling_path
from mnemo.core.extract.inbox.rendering import _render_page
from mnemo.core.extract.inbox.sources import union_with_prior_sources
from mnemo.core.extract.inbox.types import ApplyResult, ExtractedPage
from mnemo.core.extract.scanner import ExtractionState, StateEntry
from mnemo.core.rule_activation.index import is_universal, projects_for_rule


@functools.lru_cache(maxsize=1)
def _universal_threshold() -> int:
    """Read ``scoping.universalThreshold`` from config once per process.

    Predicate is invoked once per dispatched page so the cost of repeated
    config reads would be measurable on extract runs that touch many pages.
    Tests that need a different threshold can clear the cache with
    ``_universal_threshold.cache_clear()``.
    """
    from mnemo.core.config import load_config
    return int(load_config().get("scoping", {}).get("universalThreshold", 2))


def merged_projects_for(
    page: ExtractedPage, entry: StateEntry | None,
) -> list[str]:
    """Union page's sources with the prior entry's sources and resolve to projects.

    Exposed so the apply-time predicate and the end-of-extract reconciler
    can both compute the same set without re-walking the union helper.
    """
    prior = list(entry.source_files) if entry is not None else []
    merged_sources: list[str] = []
    seen: set[str] = set()
    for s in prior + list(page.source_files):
        if s and s not in seen:
            seen.add(s)
            merged_sources.append(s)
    return projects_for_rule(merged_sources)


def _apply_universal_promotion(
    page: ExtractedPage,
    entry: StateEntry | None,
    target: Path,
    vault_root: Path,
    state: ExtractionState,
    run_id: str,
    force: bool,
    result: ApplyResult,
) -> None:
    """Move (or freshly write) the page into ``shared/<type>/<slug>.md``.

    Three on-disk outcomes:

    - **Clean promotion** (dest doesn't exist): atomic_write the rendered
      page to the sacred dir, delete the ``_inbox/`` staging copy, advance
      state to status=``promoted``.
    - **Safe overwrite** (dest exists, disk hash matches the entry's
      written_hash): same as clean promotion; the existing copy was
      plugin-managed and unedited.
    - **User edit guard** (dest exists with divergent hash): bounce a
      ``.proposed.md`` sibling back into ``shared/_inbox/<type>/`` and
      leave both copies in place; the entry stays at status="inbox" so the
      next extraction tries again.

    ``target`` is the path the dispatcher computed (``shared/_inbox/<type>/...``)
    and is used only for the "where to delete the staging file" branch — the
    final destination is recomputed via :func:`_promoted_path` so this
    handler can also be called by the end-of-extract reconciler which
    synthesizes pages from on-disk inbox copies.
    """
    merged_page = union_with_prior_sources(page, entry)
    key = f"{merged_page.type}/{merged_page.slug}"
    dest = _promoted_path(vault_root, merged_page)
    content = _render_page(merged_page, run_id=run_id, auto_promoted=True)
    new_written_hash = content_hash(content)

    user_edited = False
    if dest.exists():
        disk_hash = content_hash(dest)
        if entry is None or disk_hash != entry.written_hash:
            user_edited = True

    if user_edited:
        sibling = _sibling_path(dest, vault_root)
        atomic_write(sibling, content)
        result.sibling_bounced.append((key, str(sibling)))
        return

    atomic_write(dest, content)

    # Remove the staging copy (might not exist when called from the
    # dispatch with `is_auto=True`, but the predicate already guarantees
    # `is_auto=False`, so the inbox copy *is* the page that just arrived).
    inbox_copy = _inbox_path(vault_root, merged_page)
    if inbox_copy.exists() and inbox_copy != dest:
        try:
            inbox_copy.unlink()
        except OSError:
            pass

    if entry is None:
        state.entries[key] = StateEntry(
            source_files=list(merged_page.source_files),
            source_hash=merged_page.source_hash,
            written_hash=new_written_hash,
            written_at=run_id,
            status="promoted",
            last_sync=run_id,
        )
    else:
        entry.mark_written(
            run_id=run_id,
            new_hash=new_written_hash,
            source_files=merged_page.source_files,
            source_hash=merged_page.source_hash,
            status="promoted",
        )
    result.universal_promoted.append(key)
