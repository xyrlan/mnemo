"""Apply branch for re-emission of an already-auto_promoted page.

Triggered when ``apply_pages`` sees a multi-source page whose state entry
is ``status="auto_promoted"`` — the LLM has produced a fresh consolidation
of a slug that was previously single-source. Stages a sibling
``.proposed.md`` for human review WITHOUT touching the existing
auto_promoted state entry or the sacred file.

Replaces the inline ``vault_root / "shared" / "_inbox" / page.type /
f"{page.slug}.proposed.md"`` rebuild from the pre-v0.9 monolith with a
``paths._sibling_path`` call (D2 consolidation, PR I).
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.extract.inbox.io import atomic_write
from mnemo.core.extract.inbox.paths import _promoted_path, _sibling_path
from mnemo.core.extract.inbox.rendering import _render_page
from mnemo.core.extract.inbox.types import ApplyResult, ExtractedPage
from mnemo.core.extract.scanner import StateEntry


def _apply_upgrade_proposed(
    page: ExtractedPage,
    entry: StateEntry,
    vault_root: Path,
    run_id: str,
    result: ApplyResult,
) -> None:
    """Multi-source re-emission of a slug that was already auto_promoted.

    Writes a sibling proposal into _inbox/ WITHOUT touching the existing
    auto_promoted state entry or the sacred file.  The user decides whether
    to merge the upgrade by hand.
    """
    key = f"{page.type}/{page.slug}"
    content = _render_page(page, run_id=run_id, auto_promoted=False)
    sibling = _sibling_path(_promoted_path(vault_root, page), vault_root)
    atomic_write(sibling, content)
    result.upgrade_proposed.append((key, str(sibling)))
