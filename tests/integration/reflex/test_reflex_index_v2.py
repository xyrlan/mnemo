"""#779: the compact v2 index ranks exactly as the v1 file did.

v1 wrote the in-memory index with ``indent=2``, every posting a dict with the
slug and all six field counts. v2 writes doc ids and one string per term. On
the golden vault, the in-memory index, a v1 file and a v2 file must give the
same BM25F scores — to the bit — and the same decision for every prompt.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mnemo.core.reflex import bm25
from mnemo.core.reflex import index as reflex_index
from mnemo.core.reflex.decide import decide, doc_token_sets
from mnemo.core.reflex.tokenizer import tokenize_query

FIX = Path(__file__).parent / "fixtures"
PROMPTS = [c["prompt"] for c in json.loads((FIX / "golden_prompts.json").read_text(encoding="utf-8"))]
REFLEX_CFG = {"thresholds": {"relativeGap": 1.0}}


@pytest.fixture
def golden_vault(tmp_path: Path) -> Path:
    dst = tmp_path / "vault"
    shutil.copytree(FIX / "golden_vault", dst)
    return dst


def _write_v1(vault: Path, index: dict) -> None:
    """``index`` as v1 ``write_index`` wrote it."""
    path = vault / ".mnemo" / reflex_index.INDEX_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(index, schema_version=1), indent=2), encoding="utf-8")


def _project(index: dict) -> str:
    return next(p for d in index["docs"].values() for p in d["projects"])


def test_v2_postings_read_back_as_the_index_that_was_written(golden_vault):
    built = reflex_index.build_index(golden_vault)
    reflex_index.write_index(golden_vault, built)
    loaded = reflex_index.load_index(golden_vault)

    assert isinstance(loaded["postings"], reflex_index.LazyPostings)
    assert len(loaded["postings"]) == len(built["postings"])
    assert {t: loaded["postings"][t] for t in loaded["postings"]} == built["postings"]
    assert loaded["docs"] == built["docs"]
    assert loaded["avg_field_length"] == built["avg_field_length"]
    assert loaded["doc_count"] == built["doc_count"]
    assert "doc_ids" not in loaded


def test_v1_v2_and_in_memory_give_the_same_bm25f_scores(golden_vault, tmp_path):
    built = reflex_index.build_index(golden_vault)
    v1_vault, v2_vault = tmp_path / "v1", tmp_path / "v2"
    _write_v1(v1_vault, built)
    (v2_vault / ".mnemo").mkdir(parents=True)
    reflex_index.write_index(v2_vault, built)
    v1 = reflex_index.load_index(v1_vault)
    v2 = reflex_index.load_index(v2_vault)
    assert v1["schema_version"] == 1
    assert v2["schema_version"] == reflex_index.SCHEMA_VERSION == 2

    slugs = list(built["docs"])
    nonzero = 0
    for prompt in PROMPTS:
        q = tokenize_query(prompt)
        expected = bm25.score_docs(built, query_tokens=q, candidate_slugs=slugs)
        assert bm25.score_docs(v1, query_tokens=q, candidate_slugs=slugs) == expected
        assert bm25.score_docs(v2, query_tokens=q, candidate_slugs=slugs) == expected
        nonzero += bool(expected)
    assert nonzero >= len(PROMPTS) // 2  # the comparison is over real rankings


def test_v1_and_v2_files_make_the_same_decision_on_every_prompt(golden_vault, tmp_path):
    built = reflex_index.build_index(golden_vault)
    v1_vault, v2_vault = tmp_path / "v1", tmp_path / "v2"
    _write_v1(v1_vault, built)
    (v2_vault / ".mnemo").mkdir(parents=True)
    reflex_index.write_index(v2_vault, built)
    project = _project(built)

    accepted = 0
    for prompt in PROMPTS:
        old = decide(lambda: reflex_index.load_index(v1_vault), project=project,
                     prompt=prompt, reflex_cfg=REFLEX_CFG)
        new = decide(lambda: reflex_index.load_index(v2_vault), project=project,
                     prompt=prompt, reflex_cfg=REFLEX_CFG)
        assert (new.accepted, new.silence_reason, new.scores, new.thresholds) == \
            (old.accepted, old.silence_reason, old.scores, old.thresholds)
        accepted += bool(new.accepted)
    assert accepted  # some prompt got past the gates, so the overlap gate ran


def test_query_term_token_sets_answer_the_overlap_gate_like_the_full_ones(golden_vault):
    built = reflex_index.build_index(golden_vault)
    slugs = list(built["docs"])
    for prompt in PROMPTS:
        q = tokenize_query(prompt)
        full = doc_token_sets(built, slugs)
        narrow = doc_token_sets(built, slugs, q)
        for slug in slugs:
            assert narrow[slug] == full[slug] & set(q)


def test_a_decision_on_a_v2_file_decodes_only_the_query_terms(golden_vault):
    reflex_index.write_index(golden_vault, reflex_index.build_index(golden_vault))
    loaded = reflex_index.load_index(golden_vault)
    prompt = PROMPTS[0]
    decide(loaded, project=_project(loaded), prompt=prompt, reflex_cfg=REFLEX_CFG)

    decoded = set(loaded["postings"]._decoded)
    assert decoded
    assert decoded <= set(tokenize_query(prompt))


def test_the_v2_file_is_compact(golden_vault):
    built = reflex_index.build_index(golden_vault)
    reflex_index.write_index(golden_vault, built)
    raw = (golden_vault / ".mnemo" / reflex_index.INDEX_FILENAME).read_bytes()
    data = json.loads(raw)

    assert b"\n" not in raw
    assert data["doc_ids"] == list(built["docs"])
    assert all(isinstance(v, str) for v in data["postings"].values())
    assert len(raw) * 3 < len(json.dumps(dict(built, schema_version=1), indent=2))
