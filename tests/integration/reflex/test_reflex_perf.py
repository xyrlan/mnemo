"""Perf budget: UserPromptSubmit hook <100ms P95 on 500-rule vault."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from mnemo.core.reflex import bm25
from mnemo.core.reflex.index import build_index
from mnemo.core.reflex.tokenizer import tokenize_query


@pytest.fixture
def big_vault(tmp_vault: Path) -> Path:
    fb = tmp_vault / "shared" / "feedback"
    fb.mkdir(parents=True, exist_ok=True)
    for i in range(500):
        (fb / f"rule-{i:04d}.md").write_text(
            "---\n"
            f"name: rule-{i:04d}\n"
            f"description: Description for rule {i} about various code patterns\n"
            "tags:\n"
            f"  - topic-{i % 15}\n"
            "sources:\n"
            f"  - bots/proj-{i % 5}/memory/m.md\n"
            "stability: stable\n"
            "---\n"
            f"Body for rule {i} covering patterns like {' '.join(f'word{j}' for j in range(30))}\n",
            encoding="utf-8",
        )
    return tmp_vault


def test_bm25f_scoring_under_100ms_p95_for_500_docs(big_vault: Path):
    idx = build_index(big_vault, universal_threshold=2)
    candidates = list(idx["docs"].keys())
    prompts = [
        "how do I mock topic-3 rule word5",
        "best pattern for rule-0042 description",
        "refactor topic-7 using word10 word15",
    ] * 10  # 30 runs

    timings: list[float] = []
    for p in prompts:
        q = tokenize_query(p)
        t0 = time.perf_counter()
        bm25.score_docs(idx, query_tokens=q, candidate_slugs=candidates)
        timings.append((time.perf_counter() - t0) * 1000.0)

    timings.sort()
    p50 = timings[len(timings) // 2]
    p95 = timings[int(len(timings) * 0.95)]
    assert p50 < 30.0, f"p50={p50:.1f}ms over budget"
    assert p95 < 100.0, f"p95={p95:.1f}ms over budget"
