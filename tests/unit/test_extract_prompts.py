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
