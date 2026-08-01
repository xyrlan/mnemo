"""Source-list helpers shared by inbox apply branches.

Originally lived inside :mod:`branches.auto_promoted` (PR #86) but is now
shared with :mod:`branches.universal_promotion`, so it lives one level up
to avoid an inter-branch import dependency.

Leaf module — depends only on :mod:`inbox.types` + :mod:`extract.scanner`.
"""
from __future__ import annotations

from dataclasses import replace

from mnemo.core.extract.inbox.types import ExtractedPage
from mnemo.core.extract.scanner import StateEntry


def union_with_prior_sources(page: ExtractedPage, entry: StateEntry | None) -> ExtractedPage:
    """Return a copy of *page* whose ``source_files`` unions the prior
    state-entry sources with the page's freshly-extracted sources.

    Without this union, re-extracting the same rule from a different
    project's briefings overwrites ``source_files``, so the rule's
    project count never crosses ``scoping.universalThreshold`` (=2).
    This is the universal-promotion blocker observed in the v0.15 dogfood.

    Preserves order: prior sources first (so the file's history-of-mention
    stays stable), then any new sources not already present.

    Built with :func:`dataclasses.replace` rather than a field-by-field
    rebuild: ``source_files`` is the only field that changes, and copying the
    rest by hand is how ``origin_backfill`` got silently dropped here in the
    first place (Task 6b). ``replace`` carries any future field automatically.
    """
    if entry is None or not entry.source_files:
        return page
    seen: set[str] = set()
    merged: list[str] = []
    for s in list(entry.source_files) + list(page.source_files):
        if s and s not in seen:
            seen.add(s)
            merged.append(s)
    if merged == list(page.source_files):
        return page
    return replace(page, source_files=merged)
