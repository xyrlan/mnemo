"""Harvest prompt shape: importable, constrains types, embeds the transcript."""
from __future__ import annotations

from mnemo.core.extract import prompts


def test_system_prompt_is_exported_and_nonempty():
    assert isinstance(prompts.HARVEST_SYSTEM_PROMPT, str)
    assert len(prompts.HARVEST_SYSTEM_PROMPT) > 200


def test_system_prompt_names_every_valid_type():
    text = prompts.HARVEST_SYSTEM_PROMPT
    for t in ("feedback", "user", "reference", "project"):
        assert t in text


def test_system_prompt_demands_json_pages_array():
    text = prompts.HARVEST_SYSTEM_PROMPT
    assert '"pages"' in text
    assert "JSON" in text


def test_user_prompt_wraps_the_transcript():
    out = prompts.build_harvest_prompt("USER: do the thing")
    assert "USER: do the thing" in out
    assert "=== TRANSCRIPT ===" in out
    assert "=== END TRANSCRIPT ===" in out
