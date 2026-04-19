"""Pure-stdlib tokenizer for Reflex BM25F scoring.

Design:
- Lowercase + split on ``[^a-z0-9_-]+``. Preserves kebab-case and
  snake_case tokens (package-management, path_globs).
- Strips Markdown fenced code blocks from prompts BEFORE tokenization.
  Pasted stack traces and code examples have thousands of tokens that
  tank BM25 precision; the design principle is "match user intent, not
  artefacts they pasted."
- Caps queries at 200 tokens post-stopword.

NO stemming. Code terms ``mock`` and ``mocking`` legitimately appear both
in prompts and rule bodies, so stemming would merge concepts that users
intentionally keep distinct.
"""
from __future__ import annotations

import re

from mnemo.core.reflex.stopwords import is_stopword

_TOKEN_RE = re.compile(r"[a-z0-9_\-]+")
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_MAX_QUERY_TOKENS = 200


def _strip_fenced_code(text: str) -> str:
    return _FENCE_RE.sub(" ", text)


def tokenize(text: str) -> list[str]:
    """Lowercase + split. No stopword removal, no truncation."""
    return _TOKEN_RE.findall(text.lower())


def tokenize_query(prompt: str) -> list[str]:
    """Tokenize a user prompt for BM25F scoring.

    Pipeline: strip fenced code -> tokenize -> drop stopwords -> cap at 200.
    """
    body = _strip_fenced_code(prompt)
    tokens = tokenize(body)
    kept = [t for t in tokens if not is_stopword(t)]
    return kept[:_MAX_QUERY_TOKENS]
