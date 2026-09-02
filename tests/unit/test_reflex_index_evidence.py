from __future__ import annotations

from pathlib import Path

from mnemo.core.config import DEFAULTS
from mnemo.core.reflex import bm25
from mnemo.core.reflex.index import build_index


def _rule(root: Path, slug: str, quote: str | None):
    d = root / "shared" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    ev = f"evidence:\n  quote: '{quote}'\n  source: bots/a/briefings/sessions/1.md\n" if quote else ""
    (d / f"{slug}.md").write_text(
        f"---\nname: Rule {slug}\nslug: {slug}\ndescription: d\ntype: feedback\nconfidence: verified\n{ev}"
        f"sources:\n  - bots/a/briefings/sessions/1.md\ntags:\n  - x\n---\nbody text\n")


def test_evidence_quote_is_a_scored_field(tmp_path):
    _rule(tmp_path, "with-quote", "never retry on 4xx only on 5xx")
    _rule(tmp_path, "without", None)
    idx = build_index(tmp_path)
    assert "evidence" in idx["avg_field_length"]
    assert idx["docs"]["with-quote"]["field_length"]["evidence"] > 0
    assert idx["docs"]["without"]["field_length"]["evidence"] == 0
    scores = dict(bm25.score_docs(idx, query_tokens=["retry", "4xx"], candidate_slugs=["with-quote", "without"]))
    assert scores.get("with-quote", 0) > scores.get("without", 0)


def test_default_weights_agree_between_bm25_and_config():
    assert bm25.DEFAULT_WEIGHTS["evidence"] == 2.5
    assert DEFAULTS["reflex"]["bm25f"]["fieldWeights"]["evidence"] == 2.5


def test_old_index_without_evidence_field_still_scores(tmp_path):
    """An index built before the field existed has no 'evidence' tf/length; scoring must tolerate it."""
    _rule(tmp_path, "r", None)
    idx = build_index(tmp_path)
    for doc in idx["docs"].values():
        doc["field_length"].pop("evidence", None)
    idx["avg_field_length"].pop("evidence", None)
    for entries in idx["postings"].values():
        for e in entries:
            e["tf"].pop("evidence", None)
    assert bm25.score_docs(idx, query_tokens=["body", "text"], candidate_slugs=["r"])
