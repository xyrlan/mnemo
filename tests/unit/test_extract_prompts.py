"""Unit tests for core/extract/prompts.py."""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo.core.extract import prompts, scanner


def _mk_file(agent: str, type_: str, stem: str, body: str = "body text") -> scanner.MemoryFile:
    return scanner.MemoryFile(
        path=Path(f"bots/{agent}/memory/{stem}.md"),
        agent=agent,
        type=type_,
        slug=scanner._normalize_slug(stem),
        frontmatter={"name": stem, "description": "desc", "type": type_},
        body=body,
        source_hash=f"sha256:{stem}",
    )


def test_chunks_for_under_threshold_returns_single_chunk():
    files = [_mk_file("a", "feedback", f"f{i}") for i in range(5)]
    chunks = list(prompts.chunks_for(files, chunk_size=10))
    assert len(chunks) == 1
    assert len(chunks[0]) == 5


def test_chunks_for_exact_threshold_returns_single_chunk():
    files = [_mk_file("a", "feedback", f"f{i}") for i in range(10)]
    chunks = list(prompts.chunks_for(files, chunk_size=10))
    assert len(chunks) == 1


def test_chunks_for_over_threshold_splits():
    files = [_mk_file("a", "feedback", f"f{i}") for i in range(15)]
    chunks = list(prompts.chunks_for(files, chunk_size=10))
    assert len(chunks) == 2
    assert len(chunks[0]) == 10
    assert len(chunks[1]) == 5


def test_chunks_for_preserves_order():
    files = [_mk_file("a", "feedback", f"f{i}") for i in range(12)]
    chunks = list(prompts.chunks_for(files, chunk_size=5))
    flat = [f for c in chunks for f in c]
    assert flat == files


def test_build_feedback_prompt_contains_structure():
    files = [_mk_file("agent-a", "feedback", "feedback_use_yarn")]
    prompt = prompts.build_feedback_prompt(files)
    assert "pages" in prompt  # schema example
    assert '"slug"' in prompt
    assert "<<<FILE:" in prompt
    assert "feedback_use_yarn" in prompt
    assert "body text" in prompt
    # schema hints presence
    assert "source_files" in prompt


def test_build_feedback_prompt_includes_all_files():
    files = [
        _mk_file("a", "feedback", "feedback_use_yarn", body="yarn body"),
        _mk_file("b", "feedback", "feedback_no_commits", body="no commits body"),
    ]
    prompt = prompts.build_feedback_prompt(files)
    assert "yarn body" in prompt
    assert "no commits body" in prompt
    assert prompt.count("<<<FILE:") == 2
    assert prompt.count("<<<END>>>") == 2


def test_build_user_prompt_uses_user_specific_language():
    files = [_mk_file("a", "user", "user_role")]
    prompt = prompts.build_user_prompt(files)
    assert "user" in prompt.lower()
    assert "<<<FILE:" in prompt


def test_build_reference_prompt_uses_reference_specific_language():
    files = [_mk_file("a", "reference", "reference_linear")]
    prompt = prompts.build_reference_prompt(files)
    assert "reference" in prompt.lower()
    assert "<<<FILE:" in prompt


def test_system_prompts_are_non_empty_constants():
    assert isinstance(prompts.FEEDBACK_SYSTEM_PROMPT, str)
    assert len(prompts.FEEDBACK_SYSTEM_PROMPT) > 50
    assert isinstance(prompts.USER_SYSTEM_PROMPT, str)
    assert isinstance(prompts.REFERENCE_SYSTEM_PROMPT, str)


# --- Issue #5: cross-agent merge strengthening --------------------------------


def test_feedback_system_prompt_instructs_merge_on_different_wording():
    """Two files expressing the same rule with different wording MUST merge."""
    sysp = prompts.FEEDBACK_SYSTEM_PROMPT.lower()
    assert "different" in sysp and "wording" in sysp, (
        "system prompt must explicitly tell the model to merge across different wording"
    )
    assert "when in doubt, merge" in sysp, (
        "system prompt must bias the model toward merging on ambiguity"
    )


def test_feedback_few_shot_includes_no_commits_cross_agent_merge():
    """Few-shot must demonstrate the real no-commits vs no-commit-without-permission merge."""
    files = [_mk_file("x", "feedback", "example")]
    prompt = prompts.build_feedback_prompt(files)
    assert "no_commits" in prompt or "no-commits" in prompt
    assert "no_commit_without_permission" in prompt or "no-commit-without-permission" in prompt
    # Both sources must appear in the merged output's source_files list
    assert "feedback_no_commits.md" in prompt
    assert "feedback_no_commit_without_permission.md" in prompt


# --- v0.3.1: stability schema field ------------------------------------------


def test_schema_example_contains_stability_field():
    """v0.3.1: the JSON schema example must advertise the stability field."""
    files = [_mk_file("x", "feedback", "example")]
    prompt = prompts.build_feedback_prompt(files)
    assert '"stability"' in prompt, (
        "schema example must document the stability field so the LLM emits it"
    )


def test_feedback_system_prompt_instructs_stability_markers():
    """System prompt must describe when to emit stability=evolving vs stable."""
    sysp = prompts.FEEDBACK_SYSTEM_PROMPT.lower()
    assert "stability" in sysp
    # Linguistic markers that trigger evolving
    assert "evolving" in sysp
    assert "stable" in sysp
    # Bias toward stable on ambiguity
    assert "default" in sysp and "stable" in sysp


def test_feedback_few_shot_shows_evolving_example():
    """At least one few-shot output must include a stability field value."""
    files = [_mk_file("x", "feedback", "example")]
    prompt = prompts.build_feedback_prompt(files)
    # The few-shots produce JSON; any output should now include the field.
    assert '"stability"' in prompt


def test_feedback_few_shot_includes_negative_do_not_merge_example():
    """Few-shot must include a negative example: two distinct rules that stay split."""
    files = [_mk_file("x", "feedback", "example")]
    prompt = prompts.build_feedback_prompt(files)
    lowered = prompt.lower()
    # Must signal "do NOT merge" or an equivalent anti-merge marker
    assert "do not merge" in lowered or "must not merge" in lowered or "stay separate" in lowered, (
        "few-shot must include explicit negative example flagging files that should NOT merge"
    )
    # The negative pair is use-yarn vs no-commits (distinct domains)
    assert "yarn" in lowered
    # And the negative output must emit TWO separate pages (distinct slugs)
    # sharing a single pages array — a clear demonstration of non-merge.
    assert '"slug":"use-yarn"' in prompt
    # A second distinct slug in the same few-shot confirms two pages
    assert prompt.count('"slug":"') >= 3, (
        "few-shot should show at least 3 slugs across examples "
        "(positive merge + negative 2-page + passthrough)"
    )
