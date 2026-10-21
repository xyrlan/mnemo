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


def _p50_p95_ms(score, idx: dict) -> tuple:
    """Score 30 prompts; the p50 and p95 of the CPU time each took, in ms.

    CPU time of this thread, not wall time: under pytest-xdist every worker
    competes for the CPU, and a scorer descheduled for 100 ms failed the
    budget on code that was correct (#750). Scoring is pure computation over
    the in-memory index — it never waits on I/O or a lock — so what it costs
    is the CPU it burns, and a slower scorer burns more of it under any load.
    On Windows the thread clock ticks every ~16 ms: a sample is 0 or a tick,
    far below either bound."""
    candidates = list(idx["docs"].keys())
    prompts = [
        "how do I mock topic-3 rule word5",
        "best pattern for rule-0042 description",
        "refactor topic-7 using word10 word15",
    ] * 10  # 30 runs

    timings: list[float] = []
    for p in prompts:
        q = tokenize_query(p)
        t0 = time.thread_time()
        score(idx, query_tokens=q, candidate_slugs=candidates)
        timings.append((time.thread_time() - t0) * 1000.0)

    timings.sort()
    return timings[len(timings) // 2], timings[int(len(timings) * 0.95)]


def test_bm25f_scoring_under_100ms_p95_for_500_docs(big_vault: Path):
    idx = build_index(big_vault, universal_threshold=2)
    p50, p95 = _p50_p95_ms(bm25.score_docs, idx)
    assert p50 < 30.0, f"p50={p50:.1f}ms over budget"
    assert p95 < 100.0, f"p95={p95:.1f}ms over budget"


def test_the_p95_budget_fails_a_scorer_slow_on_one_prompt_in_ten(big_vault: Path):
    """The budget above catches a slow scorer, and in the tail alone: three
    calls in thirty that burn 150 ms of CPU fail p95 while p50 stays fast."""
    idx = build_index(big_vault, universal_threshold=2)
    calls = [0]

    def slow_scorer(index, **kw):
        calls[0] += 1
        if calls[0] % 10 == 0:
            until = time.thread_time() + 0.15
            while time.thread_time() < until:
                pass
        return bm25.score_docs(index, **kw)

    p50, p95 = _p50_p95_ms(slow_scorer, idx)
    assert p50 < 30.0
    assert p95 >= 100.0, f"p95={p95:.1f}ms: a 150 ms scorer passed the budget"
