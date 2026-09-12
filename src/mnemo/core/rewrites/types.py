"""Dataclasses shared by the rewrites halves.

``classify`` (planning) and ``apply`` (execution) both need these, and importing
either from the other would be circular — same split as ``reclassify_types.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

#: Body-diff classification of a staged rewrite.
#:
#: ``insert_only`` — every non-equal opcode is an insert, so the proposal body is
#: a superset of the live body and merging drops nothing. Auto-mergeable.
#: ``full_rewrite`` — the proposal preserves none of the live body. The live rule
#: is superseded (measured: `MARKETPLACE_ENABLED` reads false when it is true).
#: ``mixed`` — anything between. Reworded prose interleaved with new facts.
Kind = Literal["insert_only", "mixed", "full_rewrite"]

Action = Literal["merge", "replace", "skip"]


@dataclass(frozen=True)
class Rewrite:
    proposal: Path
    live: Path
    #: ``"<type>/<slug>"`` — the ``.mnemo/extraction-state.json`` entry key.
    key: str
    kind: Kind
    #: Fraction of live non-blank body lines the proposal preserves, 0.0–1.0.
    keep_ratio: float
    inserted_lines: int
    dropped_lines: int


@dataclass
class ApplyPlan:
    run_id: str
    entries: list[tuple[Rewrite, Action]] = field(default_factory=list)


@dataclass
class ApplyReport:
    merged: int = 0
    replaced: int = 0
    skipped_count: int = 0
    archive_dir: Optional[Path] = None
    notes: list = field(default_factory=list)
    #: Rewrites that could not be applied: ``[{"key", "reason"}, ...]``.
    #: A no-op apply must never be silent, so the CLI prints these.
    skipped: list = field(default_factory=list)
