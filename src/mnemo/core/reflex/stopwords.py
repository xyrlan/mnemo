"""Conservative English + Portuguese stopword list.

Kept intentionally short — code/domain terms like ``test``, ``use``, ``mock``,
``auth``, ``react``, and ``banco`` (which is Portuguese for "database" and
also a valid rule alias) are NOT stopped. Over-stripping would gut recall on
technical prompts.

If you need to tune this list, the discipline is: only add words that are
structurally grammatical (articles, pronouns, auxiliaries) and never carry
domain meaning. When in doubt, leave it in.
"""
from __future__ import annotations

_EN = {
    "the", "a", "an", "and", "or", "but", "if", "then", "of", "to", "in", "on",
    "at", "by", "for", "from", "with", "without", "about", "as", "is", "are",
    "was", "were", "be", "been", "being", "have", "has", "had", "do", "does",
    "did", "will", "would", "should", "could", "may", "might", "can", "shall",
    "i", "you", "he", "she", "it", "we", "they", "me", "my", "your", "our",
    "this", "that", "these", "those", "there", "here", "how", "what", "when",
    "where", "why", "which", "who", "whose", "not", "no", "yes",
}

_PT = {
    "o", "a", "os", "as", "um", "uma", "uns", "umas",
    "de", "da", "do", "das", "dos",
    "em", "no", "na", "nos", "nas",
    "por", "para", "com", "sem",
    "que", "se", "e", "ou", "mas", "também",
    "é", "são", "foi", "ser", "está", "estão",
    "eu", "tu", "ele", "ela", "nós", "eles", "elas",
    "meu", "teu", "seu", "nosso", "este", "esta", "esse", "essa", "aquele",
    "como", "quando", "onde", "porque", "qual", "quais",
    "não", "sim",
}

STOPWORDS: frozenset[str] = frozenset(_EN | _PT)


def is_stopword(token: str) -> bool:
    """Case-insensitive stopword check."""
    return token.lower() in STOPWORDS
