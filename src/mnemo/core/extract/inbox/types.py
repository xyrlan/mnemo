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
