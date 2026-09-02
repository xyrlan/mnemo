"""The promotion gate for feedback pages.

A feedback rule may only enter ``shared/feedback/`` when it cites a user quote
that the source briefing's ``## Corrections`` section actually carries. That
section is itself verified against the transcript when the briefing is
written (core/corrections.py), so a verified page traces back to words the
person typed. Anything else is real-but-inferred knowledge and is staged as a
``reference`` page for review.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from mnemo.core import corrections
from mnemo.core.extract.inbox.rendering import _extract_body
from mnemo.core.extract.inbox.types import ExtractedPage


def _source_path(vault_root: Path, rel: str) -> Path | None:
    """Resolve a vault-relative source; refuse anything escaping the vault."""
    candidate = (vault_root / rel).resolve()
    try:
        candidate.relative_to(vault_root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def quote_verified(evidence: dict | None, vault_root: Path) -> bool:
    if not isinstance(evidence, dict):
        return False
    quote = str(evidence.get("quote") or "")
    src = _source_path(vault_root, str(evidence.get("source") or ""))
    if src is None or not quote.strip():
        return False
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    items = corrections.parse_section(_extract_body(text))
    return any(corrections.quote_matches_turn(quote, it.quote) for it in items)


def verify_page(page: ExtractedPage, vault_root: Path) -> ExtractedPage:
    """Return the page marked verified, or demoted to a staged reference page."""
    if page.type != "feedback":
        return page
    if quote_verified(page.evidence, vault_root):
        return replace(page, confidence="verified", unverified_feedback=False)
    return replace(
        page,
        type="reference",
        confidence="inferred",
        unverified_feedback=True,
        evidence=None,
    )
