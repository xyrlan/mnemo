"""A new page that says what an existing rule says reinforces it instead of minting a slug."""
from __future__ import annotations

from pathlib import Path

from mnemo.core.extract.inbox import apply_pages, dedup
from mnemo.core.extract.inbox.io import content_hash
from mnemo.core.extract.inbox.types import ExtractedPage
from mnemo.core.extract.scanner import ExtractionState, StateEntry

BODY = ("Apply negative keywords early in a campaign launch to filter irrelevant traffic.\n\n"
        "**Why:** broad match wastes budget on unrelated queries.\n\n"
        "**How to apply:** add the negative list before enabling broad match.")


def _seed(root: Path, state: ExtractionState, slug: str, name: str, body: str = BODY):
    d = root / "shared" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slug}.md"
    path.write_text(f"---\nname: {name}\ndescription: {name}\ntype: feedback\nsources:\n  - bots/a/briefings/sessions/1.md\ntags:\n  - ads\n---\n{body}\n")
    # written_hash must match the file on disk: the auto-promoted branch treats a
    # mismatch as "user edited this page" and bounces to a .proposed.md sibling
    # without touching the entry, which is not the path under test here.
    state.entries[f"feedback/{slug}"] = StateEntry(source_files=["bots/a/briefings/sessions/1.md"],
                                                   source_hash="h0", written_hash=content_hash(path),
                                                   written_at="r0", status="auto_promoted")


def _page(slug: str, name: str, body: str = BODY, sources=None):
    return ExtractedPage(slug=slug, type="feedback", name=name, description=name, body=body,
                         source_files=sources or ["bots/b/briefings/sessions/2.md"], source_hash="h1",
                         confidence="verified",
                         evidence={"quote": "q", "source": "bots/b/briefings/sessions/2.md"})


def test_similar_page_redirects_to_existing_slug_and_accrues_sources(tmp_path):
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "negative-keywords-early-launch", "Add negative keywords at launch")
    page = _page("google-ads-negative-keyword-strategy", "Negative keyword strategy for launches")
    apply_pages([page], state, tmp_path, run_id="r1")
    assert "feedback/google-ads-negative-keyword-strategy" not in state.entries
    entry = state.entries["feedback/negative-keywords-early-launch"]
    assert entry.source_files == ["bots/a/briefings/sessions/1.md", "bots/b/briefings/sessions/2.md"]
    assert not (tmp_path / "shared" / "feedback" / "google-ads-negative-keyword-strategy.md").exists()


def test_distinct_rule_keeps_its_own_slug(tmp_path):
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "negative-keywords-early-launch", "Add negative keywords at launch")
    page = _page("use-yarn", "Use yarn for package management",
                 body="Always use yarn.\n\n**Why:** yarn.lock is canonical.\n\n**How to apply:** yarn add.")
    apply_pages([page], state, tmp_path, run_id="r1")
    assert "feedback/use-yarn" in state.entries


def test_threshold_is_load_bearing(tmp_path, monkeypatch):
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "negative-keywords-early-launch", "Add negative keywords at launch")
    monkeypatch.setattr(dedup, "SIMILARITY_THRESHOLD", 1.01)
    apply_pages([_page("google-ads-negative-keyword-strategy", "Negative keyword strategy for launches")],
                state, tmp_path, run_id="r1")
    assert "feedback/google-ads-negative-keyword-strategy" in state.entries


def test_similarity_index_skips_other_types_and_missing_files(tmp_path):
    state = ExtractionState(last_run=None)
    state.entries["reference/ghost"] = StateEntry(source_files=[], source_hash="", written_hash="",
                                                  written_at="", status="auto_promoted")
    idx = dedup.SimilarityIndex(state, tmp_path, "feedback")
    assert idx.find(_page("x", "Anything at all")) is None
