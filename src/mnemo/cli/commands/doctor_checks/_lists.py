"""How far a per-item list in ``mnemo doctor`` runs (#756).

On a Windows satellite on 2026-10-27, one ``claude rm`` line over 104
session ids and 80 child-report rows made ``doctor`` about 200 lines for a
handful of findings, and buried the rows that mattered. A list shows its
first :data:`CAP` items and a line saying how many more there are and that
``mnemo doctor --all`` prints them; ``--all`` sets :func:`show_all` for the
run.
"""
from __future__ import annotations

from typing import List, Sequence, Tuple, TypeVar

T = TypeVar("T")

#: Items a list shows before it says how many more there are.
CAP = 5

_all = False


def show_all(on: bool) -> None:
    """Print every item of every list for this run (``mnemo doctor --all``)."""
    global _all
    _all = bool(on)


def capped(items: Sequence[T]) -> Tuple[List[T], int]:
    """The items to print, and how many were left out."""
    if _all or len(items) <= CAP:
        return list(items), 0
    return list(items[:CAP]), len(items) - CAP


def more(hidden: int) -> str:
    """The line that stands for what :func:`capped` left out."""
    return f"… {hidden} more — `mnemo doctor --all` lists them"
