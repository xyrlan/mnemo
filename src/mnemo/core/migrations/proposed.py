"""Relocate stray ``.proposed.md`` siblings into ``shared/_inbox/`` (#155).

When the extractor re-consolidates a page a human may have edited, it stages
the new version as a sibling file for review instead of overwriting the live
one. ``extract/inbox/paths._sibling_path`` puts that sibling under
``shared/_inbox/<type>/`` — as its docstring says, "so the sacred dir stays
free of plugin artifacts". One write site (``extract/promote.py``) used
``target.with_name`` instead and left drafts beside the rules they propose to
replace.

That is not a cosmetic misfiling. A sibling copies the live page's frontmatter
verbatim — same ``slug``, same ``name`` — so every walker keyed on slug indexes
it under the real rule's identity, and ``x.proposed.md`` sorting after ``x.md``
means the draft wins. On the vault this was found in, 30 project rules were
served to Claude as their unreviewed draft while the approved page was
unreachable.

The write site is fixed and ``core.filters`` now hides these siblings from
every walker. This migration moves the ones already on disk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mnemo.core.filters import ARCHIVE_DIR, INBOX_DIR, is_proposed_sibling


@dataclass
class ProposedReport:
    scanned: int = 0
    moved: int = 0
    #: (stray path, reason) for siblings left in place.
    skipped: list[tuple[Path, str]] = field(default_factory=list)


def _staged_destination(stray: Path, shared: Path) -> Path:
    """Where a stray sibling belongs: ``shared/_inbox/<type>/<name>``.

    Mirrors ``extract.inbox.paths._sibling_path`` for the auto-promoted case.
    Imported rather than shared because that helper takes the *target* rule
    path, while a migration only has the stray file itself.
    """
    page_type = stray.parent.name
    return shared / INBOX_DIR / page_type / stray.name


def relocate_proposed(vault_root: Path, *, dry_run: bool = False) -> ProposedReport:
    """Move ``*.proposed.md`` siblings out of ``shared/<type>/`` into
    ``shared/_inbox/<type>/``.

    Siblings already under ``_inbox`` are where they belong and are left alone.
    A stray whose destination is already occupied is skipped rather than
    overwritten: that file is an unreviewed proposal, and losing one is worse
    than leaving another misfiled for one more run.
    """
    rep = ProposedReport()
    shared = Path(vault_root) / "shared"
    if not shared.is_dir():
        return rep

    for stray in sorted(shared.rglob("*.md")):
        if not is_proposed_sibling(stray):
            continue
        rel_parts = stray.relative_to(shared).parts[:-1]
        if ARCHIVE_DIR in rel_parts:
            # ``_archive`` holds recovery copies, not drafts. ``rewrites`` (#159)
            # archives the consumed proposal under
            # ``_archive/rewrites-<run>/proposals/`` so ``--undo`` can re-stage
            # it, and ``--reject`` archives under ``rejected-<run>/``. Neither
            # shadows anything — ``is_consumer_visible`` already excludes
            # ``_archive`` — and moving them would destroy the only rollback
            # path the vault has, while re-staging rewrites already accepted.
            # Measured on the real vault: 34 archived proposals were being
            # reported as strays, and one ``mnemo extract`` would have moved
            # every one of them. Same exclusion ``iter_shared_pages`` applies.
            continue
        if INBOX_DIR in rel_parts:
            continue
        rep.scanned += 1
        dest = _staged_destination(stray, shared)
        if dest.exists():
            rep.skipped.append((stray, "a proposal is already staged there"))
            continue
        rep.moved += 1
        if dry_run:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        stray.replace(dest)
    return rep
