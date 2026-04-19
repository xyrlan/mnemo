"""All three extraction prompts must mention `aliases:` guidance (v0.8)."""
from __future__ import annotations

from mnemo.core.extract import prompts as p


def test_feedback_prompt_mentions_aliases():
    assert "aliases" in p.FEEDBACK_SYSTEM_PROMPT.lower()


def test_user_prompt_mentions_aliases():
    assert "aliases" in p.USER_SYSTEM_PROMPT.lower()


def test_reference_prompt_mentions_aliases():
    assert "aliases" in p.REFERENCE_SYSTEM_PROMPT.lower()
