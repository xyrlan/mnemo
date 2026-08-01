"""Dataclasses + typed errors for the extraction inbox package.

Leaf module — no internal dependencies. Imported by every other
``inbox/*`` module (state_io, rendering, dedup, apply, branches/*).

Behavior identical to the pre-v0.9 monolith ``inbox.py``; see
``docs/superpowers/plans/2026-04-19-refactor-roadmap.md`` PR I.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class ExtractionIOError(OSError):
    """Filesystem failure during extraction write."""


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
    # True when AT LEAST ONE source memory file carried metadata.origin:
    # backfill. The OR is deliberate and must not be "tightened" to an AND: a
    # page mixing one live and one reconstructed source is still partly
    # reconstructed, and requiring every source to be stamped let exactly such
    # a page span two projects and be universally promoted into the sacred dir
    # unreviewed (Task 6b).
    #
    # Such pages are reconstructed from archived transcripts rather than
    # observed live, so they always stage in _inbox for review — see
    # inbox/paths.py::_target_path_for_page — and never universally promote
    # (inbox/apply.py::_is_universal_promotion plus the end-of-extract
    # reconciler in extract/__init__.py).
    #
    # Derived per chunk, so it is False on every run after the one that
    # harvested the source — ``inbox/apply._resolve_sticky_origin`` restores it
    # from the durable ``StateEntry.origin_backfill`` before any gate reads it
    # (Task 9b). Set this field directly only when you know the origin; ask
    # ``backfill.origin.is_backfill_page`` when you want to read it.
    origin_backfill: bool = False


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
    # Slugs that crossed ``scoping.universalThreshold`` and were moved from
    # ``shared/_inbox/<type>/`` into ``shared/<type>/`` during this run.
    # Distinct from ``auto_promoted`` (single-source direct writes) so the
    # extraction summary can surface universal-promotion volume separately.
    universal_promoted: list[str] = field(default_factory=list)
