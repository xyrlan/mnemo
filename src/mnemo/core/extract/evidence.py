"""The promotion gate for feedback pages.

A feedback rule may only enter ``shared/feedback/`` when it cites a user quote
that one of its own ``source_files`` carries in its ``## Corrections``
section. That section is itself verified against the transcript when the
briefing is written (core/corrections.py), so a verified page traces back to
words the person typed. Anything else is real-but-inferred knowledge and is
staged as a ``reference`` page for review.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from mnemo.core import corrections
from mnemo.core.extract.inbox.rendering import _extract_body
from mnemo.core.extract.inbox.types import ExtractedPage
from mnemo.core.redact import redact_secrets


def _source_path(vault_root: Path, rel: str) -> Path | None:
    """Resolve a vault-relative source; refuse anything escaping the vault."""
    candidate = (vault_root / rel).resolve()
    try:
        candidate.relative_to(vault_root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def quote_verified(evidence: dict | None, vault_root: Path) -> bool:
    """True when the quote is specific enough to establish a rule AND appears
    verbatim in the cited briefing's ``## Corrections`` section."""
    if not isinstance(evidence, dict):
        return False
    quote = str(evidence.get("quote") or "")
    # A matching quote proves the user typed the words, not that the words
    # establish a rule: a one-line approval ("implementa os fixes") appears in
    # the Corrections of every session it closed. Same bar as reclassify (#119).
    if not corrections.quote_is_specific(quote):
        return False
    src = _source_path(vault_root, str(evidence.get("source") or ""))
    if src is None or not quote.strip():
        return False
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    items = corrections.parse_section(_extract_body(text))
    # Both sides under the same secrets pass: a briefing written since #418
    # carries ``[redacted]`` where the session typed a password, and so does
    # the quote the page stores. Idempotent, so an older unredacted briefing
    # and an older unredacted quote still meet in the middle.
    quote = redact_secrets(quote)[0]
    return any(
        corrections.quote_matches_turn(quote, redact_secrets(it.quote)[0])
        for it in items
    )


def page_verifies(evidence: dict | None, source_files, vault_root: Path) -> bool:
    """The whole bar, in one place: the quote passes :func:`quote_verified`
    **and** the briefing it cites is one of the page's own ``source_files``.

    Without the second half a page can cite any briefing in the vault and
    inherit its verification, laundering one project's correction into
    another's rule. ``mnemo replay`` re-asks this of every ``confidence:
    verified`` page on disk to tell a gate-verified label from one that
    ``mnemo reclassify`` wrote against a briefing with no ``## Corrections``
    (#257); the same predicate, so the two can never disagree.
    """
    cited = evidence.get("source") if isinstance(evidence, dict) else None
    return cited in (source_files or ()) and quote_verified(evidence, vault_root)


def verify_page(page: ExtractedPage, vault_root: Path) -> ExtractedPage:
    """Return the page marked verified, or demoted to a staged reference page.

    Verification needs a quote that passes :func:`quote_verified` — at least
    ``corrections.MIN_CONTENT_TOKENS`` content words, found in the Corrections
    of one of the page's own source briefings.

    The gate is symmetric (#244): the evidence decides the type in both
    directions. A ``feedback`` page whose quote fails is demoted to a staged
    ``reference`` page; a ``reference`` page whose quote passes the very same
    bar becomes verified ``feedback``. The LLM's ``type`` is an opinion and the
    quote is a checkable fact — on the real vault the four truest corrections
    of a month were emitted as ``reference`` *with* a verifying quote and
    counted as unbacked because only the demoting direction existed. ``user``
    and ``project`` pages are not retyped: a quote can establish a rule, not
    an identity or an architectural fact.
    """
    # The quote must come from a briefing this page was actually built from.
    # Without this a page can cite any briefing in the vault and inherit its
    # verification, laundering one project's correction into another's rule.
    verifies = page_verifies(page.evidence, page.source_files, vault_root)
    if page.type == "reference":
        if verifies:
            return replace(page, type="feedback", confidence="verified", unverified_feedback=False)
        return page
    if page.type != "feedback":
        return page
    if verifies:
        return replace(page, confidence="verified", unverified_feedback=False)
    return replace(
        page,
        type="reference",
        confidence="inferred",
        unverified_feedback=True,
        evidence=None,
    )
