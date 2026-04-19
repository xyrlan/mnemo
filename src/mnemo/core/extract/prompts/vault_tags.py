"""Existing-vault-tags fragment shown in the consolidation user message.

Extracted verbatim from the legacy ``mnemo.core.extract.prompts``
monolith in v0.9 PR F2.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.filters import collect_existing_tags


def _existing_tags_fragment(vault_root: Path | None, page_type: str) -> str:
    """Render the controlled-vocabulary hint shown in the user message.

    Per-page-type scope: feedback prompts see only tags from
    ``shared/feedback/``, etc. Keeps vocab domains separate (v0.4 decision).
    Returns an empty string when no vault_root is provided or no tags exist
    yet (fresh vault), so prompts stay valid in test fixtures and first runs.
    """
    if vault_root is None:
        return ""
    existing = collect_existing_tags(vault_root, page_type)
    if not existing:
        return (
            f"Existing vault tags for {page_type}: (none yet — this is an "
            f"early extraction; invent clean kebab-case topics like "
            f"\"git\", \"workflow\", \"auth\").\n\n"
        )
    return (
        f"Existing vault tags for {page_type}: {existing}. Prefer reusing "
        f"these exact strings; only invent a new tag when none of the above fit.\n\n"
    )
