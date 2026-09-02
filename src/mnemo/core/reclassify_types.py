"""Dataclasses and the frontmatter splitter shared by the reclassify halves.

``reclassify`` (planning) and ``reclassify_apply`` (execution) both need these,
and importing either from the other would be circular — so they live here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mnemo.core.filters import parse_frontmatter


@dataclass(frozen=True)
class RuleDoc:
    path: Path
    slug: str
    name: str
    fm: dict
    body: str
    sources: list


@dataclass
class Verdict:
    slug: str
    verdict: str
    target: Optional[str] = None
    quote: Optional[str] = None
    source: Optional[str] = None
    reason: str = ""


@dataclass
class Plan:
    run_id: str
    llm_calls: int
    verdicts: list


@dataclass
class ApplyReport:
    kept: int = 0
    demoted: int = 0
    merged: int = 0
    archived: int = 0
    archive_dir: Optional[Path] = None
    notes: list = field(default_factory=list)


def split_frontmatter(text: str) -> tuple:
    """(parsed frontmatter, body) — body is everything after the closing ``---``."""
    fm = parse_frontmatter(text)
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return fm, text[end + 5:]
    return fm, text
