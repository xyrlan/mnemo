from __future__ import annotations

from mnemo.core.reflex.tokenizer import tokenize, tokenize_query


def test_tokenize_lowercases_and_splits():
    assert tokenize("Use Prisma Mock") == ["use", "prisma", "mock"]


def test_tokenize_preserves_kebab_and_snake():
    assert tokenize("package-management path_globs") == ["package-management", "path_globs"]


def test_tokenize_query_strips_stopwords():
    # "the" and "of" are stopwords; "use", "prisma", "mock" aren't.
    assert tokenize_query("Use the Prisma mock of Jest") == ["use", "prisma", "mock", "jest"]


def test_tokenize_query_strips_fenced_code_blocks():
    prompt = """preciso mockar o prisma
```python
def test():
    prisma = Mock()
```
valeu"""
    toks = tokenize_query(prompt)
    # "def", "test", "mock" from inside the fence must NOT appear as tokens;
    # the natural-language query terms survive.
    assert "mockar" in toks
    assert "prisma" in toks
    assert "def" not in toks


def test_tokenize_query_caps_at_200_tokens():
    flood = " ".join(f"term{i}" for i in range(500))
    assert len(tokenize_query(flood)) == 200
