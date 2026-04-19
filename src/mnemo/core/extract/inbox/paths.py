"""Path-shape helpers for the v0.2 extraction inbox.

Originally extracted from the 755-line ``inbox.py`` in the v0.9 refactor
roadmap PR B (as ``extract/inbox_paths.py``) and relocated inside the
``inbox`` package in PR I. These five helpers compute filesystem targets
for extracted pages and their ``.proposed.md`` siblings; they have no
runtime dependencies beyond ``pathlib.Path`` and the ``ExtractedPage``
dataclass (imported lazily via ``TYPE_CHECKING`` to avoid a circular
import with ``inbox/types.py``).
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mnemo.core.extract.inbox.types import ExtractedPage


def _inbox_path(vault_root: Path, page: ExtractedPage) -> Path:
    return vault_root / "shared" / "_inbox" / page.type / f"{page.slug}.md"


def _promoted_path(vault_root: Path, page: ExtractedPage) -> Path:
    return vault_root / "shared" / page.type / f"{page.slug}.md"


def _target_path_for_page(page: ExtractedPage, vault_root: Path) -> Path:
    """Return the filesystem target for a page based on its source count.

    Single-source pages go directly to the sacred dir (auto-promote).
    Multi-source pages stage in _inbox/ for review.

    Routes through ``_promoted_path`` / ``_inbox_path`` so the shared
    ``shared/<type>/<slug>.md`` shape lives in exactly one place
    (kills D1 inline target construction in PR I).
    """
    if len(page.source_files) == 1:
        return _promoted_path(vault_root, page)
    return _inbox_path(vault_root, page)


def _is_auto_promoted_target(target: Path, vault_root: Path) -> bool:
    """True if the target is inside shared/<type>/ (not shared/_inbox/)."""
    try:
        rel = target.relative_to(vault_root / "shared")
    except ValueError:
        return False
    parts = rel.parts
    if not parts:
        return False
    return parts[0] != "_inbox"


def _sibling_path(target: Path, vault_root: Path) -> Path:
    """Where does a .proposed.md sibling for this target live?

    Auto-promoted targets (in shared/<type>/) bounce their siblings back into
    shared/_inbox/<type>/ so the sacred dir stays free of plugin artifacts.
    _inbox/ targets keep siblings adjacent (v0.2 behavior).
    """
    if _is_auto_promoted_target(target, vault_root):
        page_type = target.parent.name
        slug = target.stem
        return vault_root / "shared" / "_inbox" / page_type / f"{slug}.proposed.md"
    return target.parent / f"{target.stem}.proposed.md"
