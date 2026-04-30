"""Repeat-pattern detector for Tier 3 end-of-session extractor.

Heuristic-only: no LLM calls, no NLTK. Uses regex to extract verb phrases
from commit messages and simple string matching for keyword detection.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import List

# Match verb at start of message (imperative commit style) followed by a noun word
_VERB_NOUN_RE = re.compile(
    r"^([a-z]+)\s+([a-z][\w/-]+)",
    re.IGNORECASE,
)

# Common auxiliary / stop verbs to filter out from verb-phrase extraction
_STOP_VERBS = frozenset(
    ["merge", "bump", "update", "fix", "add", "remove", "revert", "wip", "init",
     "use", "make", "move", "rename", "delete", "clean", "refactor", "test",
     "chore", "docs", "style"]
)

ALWAYS_KEYWORDS = frozenset(["always", "nunca", "never", "toda vez", "sempre"])


def extract_verb_phrases(messages: List[str]) -> "Counter[str]":
    """Extract verb+noun pairs from commit messages.

    Returns a Counter mapping ``"verb noun"`` → occurrence count. Only
    imperative-style first tokens are extracted. Very common/generic verbs
    (fix, add, …) are excluded to reduce noise.
    """
    counts: Counter[str] = Counter()
    for msg in messages:
        m = _VERB_NOUN_RE.match(msg.strip())
        if not m:
            continue
        verb = m.group(1).lower()
        noun = m.group(2).lower()
        if verb in _STOP_VERBS:
            continue
        phrase = f"{verb} {noun}"
        counts[phrase] += 1
    return counts


def find_repeated_patterns(messages: List[str], min_count: int = 2) -> List[str]:
    """Return verb-phrase patterns that occur ≥ *min_count* times.

    Results are sorted by frequency descending, then alphabetically.
    """
    counts = extract_verb_phrases(messages)
    return [
        phrase
        for phrase, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        if count >= min_count
    ]


def scan_for_keywords(texts: List[str], keywords: List[str]) -> bool:
    """Return True if any *keyword* appears (case-insensitive) in any text."""
    lower_keywords = [k.lower() for k in keywords]
    for text in texts:
        lower_text = text.lower()
        for kw in lower_keywords:
            if kw in lower_text:
                return True
    return False
