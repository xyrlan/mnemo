"""Harvest prompt shape: importable, constrains types, embeds the transcript."""
from __future__ import annotations

from mnemo.core.extract import prompts
from mnemo.core.extract.scanner import _VALID_TYPES


def test_system_prompt_is_exported_and_nonempty():
    assert isinstance(prompts.HARVEST_SYSTEM_PROMPT, str)
    assert len(prompts.HARVEST_SYSTEM_PROMPT) > 200


def test_system_prompt_names_every_valid_type():
    text = prompts.HARVEST_SYSTEM_PROMPT
    for t in _VALID_TYPES:
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


def test_system_prompt_disambiguates_reference_from_project():
    """reference vs project is the one type pairing with no prior LLM-facing
    prompt in the tree to inherit calibration from — it needs an explicit
    disambiguator, not just two separate bullet definitions."""
    text = prompts.HARVEST_SYSTEM_PROMPT.lower()
    assert "outside this codebase" in text
    assert "this codebase itself" in text


def test_system_prompt_includes_few_shot_examples():
    """Type classification (4-way) + durability judgment is a harder joint
    call than any existing consolidation prompt; it needs calibration
    examples, including a zero-pages case and a reference/project case."""
    text = prompts.HARVEST_SYSTEM_PROMPT
    assert text.count('{"pages":[]}') >= 1
    assert '"type":"reference"' in text
    assert '"type":"project"' in text
