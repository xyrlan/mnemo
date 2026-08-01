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

There is a third reader, added by Task 9b: the extraction **state entry**.
The two file-level readings above are only available while the file in
question is in front of the code that needs the answer, and a harvested
memory file is dirty exactly once — so ``StateEntry.origin_backfill`` carries
the answer between runs, and :func:`is_backfill_entry` reads it.

Failure direction is deliberate: a false positive stages a live page for
review (visible, cheap, reversible); a false negative floods the sacred dir
with unreviewed reconstructions. When in doubt this predicate says yes.
"""
from __future__ import annotations

from pathlib import Path
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


def is_backfill_entry(entry: Any) -> bool:
    """True when an extraction-state entry remembers a backfill origin.

    The durable answer. ``origin_backfill`` on the page is derived from the
    *chunk being processed right now*, so it evaporates the run after the
    first (the harvested source stops being dirty and stops appearing in any
    chunk). The state entry is the one place that survives between runs, and
    ``StateEntry.origin_backfill`` is written by every branch that stages a
    backfill page.

    Same ``getattr`` tolerance as :func:`is_backfill_page`: entries loaded
    from a state file written before this field existed simply answer False,
    and :func:`is_backfill_markdown` heals those.
    """
    return bool(getattr(entry, "origin_backfill", False))


def is_backfill_markdown(path: Path) -> bool:
    """True when the markdown file at *path* carries the origin stamp.

    Used to recover the answer for vaults whose extraction state predates
    ``StateEntry.origin_backfill``: the staged page in ``shared/_inbox/`` was
    rendered with :data:`ORIGIN_LINE`, so the file itself still knows even
    when the state entry has forgotten. Missing/unreadable file → False, so
    a page that was never staged is never mistaken for a backfill page.

    Deliberately *not* in ``extract/inbox/paths.py``: that module documents
    itself as I/O-free, and this is I/O. Callers read here and pass the
    answer into the routing helpers.
    """
    from mnemo.core.extract.scanner import parse_frontmatter

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    fm, _ = parse_frontmatter(text)
    return is_backfill_frontmatter(fm)
