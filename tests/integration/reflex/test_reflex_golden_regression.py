"""Golden regression: fixed corpus of 20 rules x 30 prompts.

The vault and the prompt->expected mapping are checked into the tree so this
test catches any change in triple-gate behaviour, stopword list, or BM25F
parameters that would shift outcomes.

If you change thresholds or the tokenizer, EXPECT this test to fail and
regenerate the golden expectations deliberately -- do not paper over with
``pytest.mark.xfail``.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mnemo.core.reflex import bm25, gates
from mnemo.core.reflex.index import build_index
from mnemo.core.reflex.tokenizer import tokenize_query

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def golden_vault(tmp_path: Path) -> Path:
    dst = tmp_path / "vault"
    shutil.copytree(FIX / "golden_vault", dst)
    (dst / "mnemo.config.json").write_text(json.dumps({"vaultRoot": str(dst)}))
    (dst / "bots").mkdir(exist_ok=True)
    return dst


def test_golden_outcomes(golden_vault):
    idx = build_index(golden_vault, universal_threshold=2)
    expectations = json.loads(
        (FIX / "golden_prompts.json").read_text(encoding="utf-8")
    )

    # Precompute doc-token sets for the gate overlap check.
    doc_tokens: dict[str, set[str]] = {}
    for term, entries in idx["postings"].items():
        for entry in entries:
            doc_tokens.setdefault(entry["slug"], set()).add(term)

    failures: list[str] = []
    for case in expectations:
        q_tokens = tokenize_query(case["prompt"])
        candidates = list(idx["docs"].keys())
        scores = bm25.score_docs(
            idx, query_tokens=q_tokens, candidate_slugs=candidates
        )
        result = gates.evaluate_gates(
            scores,
            query_tokens=q_tokens,
            doc_tokens_by_slug=doc_tokens,
            thresholds=gates.DEFAULT_THRESHOLDS,
        )

        expected = case["expected"]
        if expected == "silence":
            if result.accepted_slugs:
                failures.append(
                    f"prompt={case['prompt']!r} expected silence but got "
                    f"{result.accepted_slugs} (scores[:3]={scores[:3]})"
                )
        else:
            if result.accepted_slugs[:1] != expected[:1]:
                failures.append(
                    f"prompt={case['prompt']!r} expected top-1 {expected[0]!r} "
                    f"got {result.accepted_slugs} (scores[:3]={scores[:3]})"
                )

    assert not failures, "\n".join(failures)
