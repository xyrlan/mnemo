from __future__ import annotations

from mnemo.core.reflex.stopwords import STOPWORDS, is_stopword


def test_stopwords_covers_english_function_words():
    for w in ("the", "and", "is", "of", "to", "how", "i", "a"):
        assert w in STOPWORDS, w


def test_stopwords_covers_portuguese_function_words():
    for w in ("o", "a", "de", "que", "é", "como", "para", "um", "uma"):
        assert w in STOPWORDS, w


def test_stopwords_does_not_contain_code_terms():
    # These should NEVER be stopped — they're code/domain terms.
    for w in ("prisma", "mock", "react", "auth", "use", "test", "banco", "database"):
        assert w not in STOPWORDS, w


def test_is_stopword_is_case_insensitive():
    assert is_stopword("THE")
    assert is_stopword("De")
