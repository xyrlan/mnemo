"""The demotion marker: one spelling, one predicate, three readings.

A feedback page whose quote fails :func:`extract.evidence.verify_page` is
demoted to a staged ``reference`` page carrying ``unverified_feedback=True``.
That flag is what keeps it out of ``shared/`` — and, like the backfill origin
stamp before it (:mod:`mnemo.core.backfill.origin`), it is **derived per run**
and therefore evaporates.

``verify_page`` returns a non-feedback page untouched::

    if page.type != "feedback":
        return page

From the second run onwards the staged page is advertised to the LLM as an
existing *reference* rule, so it is re-emitted under ``type: reference``, the
gate never runs on it, and the flag arrives False. It then walks through the
single-source auto-promote door in ``inbox/paths._target_path_for_page`` into
the sacred dir — rendered fresh, so without a ``demoted_from:`` line, which is
why the leak is invisible to a grep for the marker (#177).

Three readings, so the answer survives whatever the current chunk forgot:

* **the page** — this run's answer, from ``verify_page``;
* **the state entry** — ``StateEntry.unverified_feedback``, written by
  :func:`extract.inbox.apply._stamp_entry_demotion` after every dispatch;
* **the staged markdown** in ``shared/_inbox/<type>/`` — rendered with
  :data:`DEMOTED_LINE`, which heals vaults whose state file predates the field.

Failure direction matches origin's doctrine and for the same reason: a false
positive keeps a live page staged for review (visible, cheap, reversible); a
false negative puts an unverified rule in the directory the reflex injects
from. When in doubt this predicate says yes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

#: Frontmatter line stamped into a demoted page. Top-level by construction.
#: Written by ``extract.inbox.rendering._render_page``.
DEMOTED_LINE = "demoted_from: feedback\n"

#: Value of the ``demoted_from`` key on a page the evidence gate demoted.
DEMOTED_FROM = "feedback"


def is_demoted_frontmatter(fm: Any) -> bool:
    """True when parsed frontmatter carries the demotion marker.

    Only the top-level spelling exists: unlike the origin stamp, nothing
    nests this key, because the only writer is ``_render_page``.
    """
    if not isinstance(fm, dict):
        return False
    return str(fm.get("demoted_from") or "") == DEMOTED_FROM


def is_demoted_page(page: Any) -> bool:
    """True when an ``ExtractedPage``-shaped object is a demoted page.

    ``getattr`` with a default rather than attribute access, matching
    :func:`mnemo.core.backfill.origin.is_backfill_page`: several call sites
    are handed page-shaped objects from tests and older state.
    """
    return bool(getattr(page, "unverified_feedback", False))


def is_demoted_entry(entry: Any) -> bool:
    """True when an extraction-state entry remembers a demotion.

    The durable answer between runs. ``unverified_feedback`` on the page is
    this run's reading only, and the run that re-emits the slug as a native
    reference page produces no reading at all.
    """
    return bool(getattr(entry, "unverified_feedback", False))


def is_demoted_markdown(path: Path) -> bool:
    """True when the markdown file at *path* carries the demotion marker.

    Recovers the answer for vaults whose extraction state predates
    ``StateEntry.unverified_feedback``: the staged page was rendered with
    :data:`DEMOTED_LINE`, so the file still knows when the entry has
    forgotten.

    Failure directions follow :func:`mnemo.core.backfill.origin.is_backfill_markdown`
    exactly, and for the same reasons:

    * **missing → False.** A page that was never staged must not be mistaken
      for a demoted one, or every live page would stage forever.
    * **present but unreadable → True.** The marker may well be in there, and
      the caller treats True as "leave it staged" — the reversible direction.

    Undecodable bytes are not "unreadable": ``shared/_inbox/`` is a directory
    users are invited to edit by hand, so this decodes with
    ``errors="replace"`` and answers from whatever frontmatter survives.
    """
    from mnemo.core.extract.scanner import parse_frontmatter

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False
    except OSError:
        # Exists but unreadable (permissions, a directory, a dead symlink).
        # Fail safe: assume the marker is there and keep the page staged.
        return True
    fm, _ = parse_frontmatter(text)
    return is_demoted_frontmatter(fm)
