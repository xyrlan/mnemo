"""The backfill origin stamp: one spelling, one predicate, one render line.

The stamp is the *only* thing keeping LLM-reconstructed pages out of
``shared/`` until a human reviews them, and it is read on both sides of the
extraction pipeline:

* **source memory files** in ``bots/<repo>/memory/`` — written by
  :mod:`mnemo.core.backfill.harvest`, which nests the stamp under
  ``metadata:`` to match the shape live capture writes;
* **staged pages** in ``shared/_inbox/<type>/`` — written by
  ``extract.inbox.rendering._render_page`` and
  ``extract.promote._render_project_page``, which emit it top-level.

Those two files are then read by two different parsers:
``scanner.parse_frontmatter`` (flat — nested keys are lifted to the top level)
and ``filters.parse_frontmatter`` (nesting-aware — a nested key stays nested).
Cross the two spellings with the two parsers and three of the four combinations
see the stamp while one does not, which is how a doctor advisory and a routing
gate came to disagree about the same file.

:func:`is_backfill_frontmatter` accepts **either** spelling, so the answer no
longer depends on which parser the caller happened to have in hand. Call sites
keep whatever parser they already use for the rest of the frontmatter.

Writers still emit exactly one spelling each — ``ORIGIN_LINE`` for staged
pages — and tests pin that spelling at the *text* level rather than through a
parser, because a parser-level assertion cannot distinguish the two.

Failure direction is deliberate: a false positive stages a live page for
review (visible, cheap, reversible); a false negative floods the sacred dir
with unreviewed reconstructions. When in doubt this predicate says yes.
"""
from __future__ import annotations

from typing import Any

#: Value of the ``origin`` key on anything backfill produced.
BACKFILL = "backfill"

#: Frontmatter line stamped into staged pages. Top-level by construction.
ORIGIN_LINE = f"origin: {BACKFILL}\n"


def is_backfill_frontmatter(fm: Any) -> bool:
    """True when parsed frontmatter carries the backfill origin stamp.

    Accepts a top-level ``origin: backfill`` (the staged-page spelling, and
    what the flat parser yields for a harvested file) or a nested
    ``metadata: {origin: backfill}`` (what the nesting-aware parser yields for
    that same harvested file).
    """
    if not isinstance(fm, dict):
        return False
    if str(fm.get("origin") or "") == BACKFILL:
        return True
    metadata = fm.get("metadata")
    if isinstance(metadata, dict) and str(metadata.get("origin") or "") == BACKFILL:
        return True
    return False


def is_backfill_page(page: Any) -> bool:
    """True when an ``ExtractedPage``-shaped object is backfill-origin.

    ``getattr`` with a default rather than attribute access: several call
    sites are handed page-shaped objects from tests and older state that
    predate the field.
    """
    return bool(getattr(page, "origin_backfill", False))
