"""Reject pages that are the extractor's own instructions echoed back.

The vault's highest-source_count rule turned out to be a paraphrase of the
enforce-block guidance in the feedback system prompt. These phrases only ever
appear in mnemo's prompts, never in a real correction.
"""
from __future__ import annotations

from mnemo.core.extract.inbox.types import ExtractedPage

ECHO_PHRASES = (
    "blocking intent",
    "enforce block",
    "stability field",
    "tier 2 page",
    "aliases field",
    "activates_on",
    "deny_pattern",
    "sacred directory",
    "existing vault tags",
)


def is_prompt_echo(page: ExtractedPage) -> bool:
    haystack = " ".join((page.name, page.description, page.body)).lower()
    return any(phrase in haystack for phrase in ECHO_PHRASES)
