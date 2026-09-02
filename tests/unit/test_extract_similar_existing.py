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

# Same rule, different wording. Byte-identical bodies score ~0.70 and would pass
# any threshold; real re-learned rules never do. On the 2026-09-02 vault
# calibration real duplicate families sit at 0.32-0.36, so the fixtures here are
# genuine paraphrases (this pair scores 0.456 weighted / 0.429 name overlap).
PARAPHRASE = ("Add the negative keyword list at campaign launch so irrelevant traffic is filtered out early.\n\n"
              "**Why:** without it, broad match burns budget on unrelated queries.\n\n"
              "**How to apply:** load the negative keywords before you enable broad match on the campaign.")

PARAPHRASE_2 = ("Set up negative keywords right at launch time to keep irrelevant traffic out of the campaign.\n\n"
                "**Why:** broad match otherwise spends budget on unrelated queries.\n\n"
                "**How to apply:** register the negative keyword list, then turn on broad match.")


def _seed(root: Path, state: ExtractionState, slug: str, name: str, body: str = BODY,
          status: str = "auto_promoted"):
    d = root / "shared" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slug}.md"
    path.write_text(f"---\nname: {name}\ndescription: {name}\ntype: feedback\nsources:\n  - bots/a/briefings/sessions/1.md\ntags:\n  - ads\n---\n{body}\n")
    # written_hash must match the file on disk: the auto-promoted branch treats a
    # mismatch as "user edited this page" and bounces to a .proposed.md sibling
    # without touching the entry, which is not the path under test here.
    state.entries[f"feedback/{slug}"] = StateEntry(source_files=["bots/a/briefings/sessions/1.md"],
                                                   source_hash="h0", written_hash=content_hash(path),
                                                   written_at="r0", status=status)


def _page(slug: str, name: str, body: str = BODY, sources=None, source_hash="h1"):
    return ExtractedPage(slug=slug, type="feedback", name=name, description=name, body=body,
                         source_files=sources or ["bots/b/briefings/sessions/2.md"],
                         source_hash=source_hash,
                         confidence="verified",
                         evidence={"quote": "q", "source": "bots/b/briefings/sessions/2.md"})


def test_similar_page_redirects_to_existing_slug_and_accrues_sources(tmp_path):
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "negative-keywords-early-launch", "Add negative keywords at launch")
    page = _page("google-ads-negative-keyword-strategy",
                 "Negative keyword list before broad match launch",
                 body=PARAPHRASE)
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
    apply_pages([_page("google-ads-negative-keyword-strategy",
                       "Negative keyword list before broad match launch", body=PARAPHRASE)],
                state, tmp_path, run_id="r1")
    assert "feedback/google-ads-negative-keyword-strategy" in state.entries


def test_name_overlap_is_load_bearing(tmp_path, monkeypatch):
    """The second gate must be read at call time too, and must be able to veto."""
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "negative-keywords-early-launch", "Add negative keywords at launch")
    monkeypatch.setattr(dedup, "NAME_OVERLAP_MIN", 1.01)
    apply_pages([_page("google-ads-negative-keyword-strategy",
                       "Negative keyword list before broad match launch", body=PARAPHRASE)],
                state, tmp_path, run_id="r1")
    assert "feedback/google-ads-negative-keyword-strategy" in state.entries


def test_never_redirects_onto_a_dismissed_slug(tmp_path):
    """Redirecting onto a dismissed slug drops the page entirely (dismissed_skipped)."""
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "negative-keywords-early-launch", "Add negative keywords at launch",
          status="dismissed")
    page = _page("google-ads-negative-keyword-strategy",
                 "Negative keyword list before broad match launch", body=PARAPHRASE)
    apply_pages([page], state, tmp_path, run_id="r1")
    assert "feedback/google-ads-negative-keyword-strategy" in state.entries
    assert state.entries["feedback/negative-keywords-early-launch"].status == "dismissed"


def test_same_batch_duplicates_collapse_to_one_page(tmp_path):
    """Two similar NEW pages in one call: the second redirects onto the first."""
    state = ExtractionState(last_run=None)
    pages = [
        _page("negative-keywords-early-launch", "Add negative keywords at launch",
              body=BODY, sources=["bots/a/briefings/sessions/1.md"], source_hash="hA"),
        _page("google-ads-negative-keyword-strategy",
              "Negative keywords added early at launch",
              body=PARAPHRASE_2, sources=["bots/b/briefings/sessions/2.md"], source_hash="hB"),
    ]
    apply_pages(pages, state, tmp_path, run_id="r1")
    keys = [k for k in state.entries if k.startswith("feedback/")]
    assert keys == ["feedback/negative-keywords-early-launch"], keys
    assert not (tmp_path / "shared" / "feedback" / "google-ads-negative-keyword-strategy.md").exists()


def test_similarity_index_skips_other_types_and_missing_files(tmp_path):
    state = ExtractionState(last_run=None)
    state.entries["reference/ghost"] = StateEntry(source_files=[], source_hash="", written_hash="",
                                                  written_at="", status="auto_promoted")
    idx = dedup.SimilarityIndex(state, tmp_path, "feedback")
    assert idx.find(_page("x", "Anything at all")) is None


def test_find_tie_break_prefers_lexicographically_smaller_slug(tmp_path):
    """Equal scores must not resolve by state-file insertion order."""
    state = ExtractionState(last_run=None)
    # Seeded in reverse-lexicographic order so insertion order and lexicographic
    # order disagree; identical bodies/names make the two scores exactly equal.
    _seed(tmp_path, state, "zzz-negative-keywords", "Add negative keywords at launch")
    _seed(tmp_path, state, "aaa-negative-keywords", "Add negative keywords at launch")
    idx = dedup.SimilarityIndex(state, tmp_path, "feedback")
    page = _page("google-ads-negative-keyword-strategy",
                 "Negative keyword list before broad match launch", body=PARAPHRASE)
    assert idx.find(page) == "aaa-negative-keywords"
