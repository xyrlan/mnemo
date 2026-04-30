"""Tests for autopilot/proposer/_patterns.py"""
from __future__ import annotations

from mnemo.autopilot.proposer._patterns import (
    ALWAYS_KEYWORDS,
    extract_verb_phrases,
    find_repeated_patterns,
    scan_for_keywords,
)


def test_extract_verb_phrases_basic():
    messages = [
        "validate input before saving",
        "validate user email format",
        "parse config file",
        "parse response body",
    ]
    counts = extract_verb_phrases(messages)
    # "validate input" and "validate user" are separate phrases
    # But "parse config" and "parse response" are separate too
    assert counts["validate input"] == 1
    assert counts["parse config"] == 1


def test_extract_verb_phrases_ignores_stop_verbs():
    messages = [
        "fix bug in handler",
        "fix typo in readme",
        "add new feature",
        "add validation",
        "update dependencies",
    ]
    counts = extract_verb_phrases(messages)
    # All stop verbs — should produce nothing
    assert len(counts) == 0


def test_extract_verb_phrases_counts_repeated():
    messages = [
        "normalize price value",
        "normalize price format",
        "normalize price input",
    ]
    counts = extract_verb_phrases(messages)
    assert counts["normalize price"] == 3


def test_find_repeated_patterns_returns_above_threshold():
    messages = [
        "normalize price value",
        "normalize price format",
        "normalize price input",
        "validate input data",
    ]
    patterns = find_repeated_patterns(messages, min_count=2)
    assert "normalize price" in patterns
    # "validate input" only appears once — should not be included
    assert "validate input" not in patterns


def test_find_repeated_patterns_sorted_by_frequency():
    messages = [
        "normalize price a",
        "normalize price b",
        "normalize price c",
        "validate input x",
        "validate input y",
    ]
    patterns = find_repeated_patterns(messages, min_count=2)
    assert patterns[0] == "normalize price"  # 3 occurrences first
    assert patterns[1] == "validate input"   # 2 occurrences second


def test_find_repeated_patterns_empty_when_no_repeats():
    messages = [
        "normalize price",
        "validate input",
        "parse config",
    ]
    patterns = find_repeated_patterns(messages, min_count=2)
    assert patterns == []


def test_scan_for_keywords_true_when_found():
    texts = ["always use HTTPS for API calls", "just a note"]
    assert scan_for_keywords(texts, ["always"]) is True


def test_scan_for_keywords_true_for_nunca():
    texts = ["nunca deixa o campo vazio", "other text"]
    assert scan_for_keywords(texts, list(ALWAYS_KEYWORDS)) is True


def test_scan_for_keywords_false_when_absent():
    texts = ["add validation", "fix typo"]
    assert scan_for_keywords(texts, list(ALWAYS_KEYWORDS)) is False


def test_scan_for_keywords_case_insensitive():
    texts = ["ALWAYS run tests before commit"]
    assert scan_for_keywords(texts, ["always"]) is True


def test_scan_for_keywords_empty_texts():
    assert scan_for_keywords([], ["always"]) is False


def test_always_keywords_contains_expected():
    assert "always" in ALWAYS_KEYWORDS
    assert "nunca" in ALWAYS_KEYWORDS
    assert "never" in ALWAYS_KEYWORDS
