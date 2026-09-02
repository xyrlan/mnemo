"""Feedback reaches shared/feedback/ only with a quote that verifies against its briefing."""
from __future__ import annotations

from pathlib import Path

from mnemo.core.extract import evidence
from mnemo.core.extract.inbox import apply_pages
from mnemo.core.extract.inbox.paths import _target_path_for_page
from mnemo.core.extract.inbox.types import ExtractedPage
from mnemo.core.extract.scanner import ExtractionState

BRIEFING = """---
type: briefing
agent: proj
session_id: s1
corrections: 1
---

# Briefing — proj — s1

## Decisions made
- x

## Corrections
- "never retry on 4xx, only on 5xx" → Retry only on 5xx
"""


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    b = root / "bots" / "proj" / "briefings" / "sessions" / "s1.md"
    b.parent.mkdir(parents=True)
    b.write_text(BRIEFING)
    (root / "shared").mkdir()
    return root


def _page(**kw):
    base = dict(slug="retry-5xx-only", type="feedback", name="Retry only on 5xx", description="d",
                body="Retry only 5xx.", source_files=["bots/proj/briefings/sessions/s1.md"],
                source_hash="h1")
    base.update(kw)
    return ExtractedPage(**base)


def test_verified_when_quote_is_in_source_corrections(tmp_path):
    root = _vault(tmp_path)
    p = evidence.verify_page(_page(evidence={"quote": "Never retry on 4xx,  only on 5xx",
                                             "source": "bots/proj/briefings/sessions/s1.md"}), root)
    assert p.type == "feedback" and p.confidence == "verified" and not p.unverified_feedback


def test_unverified_when_quote_missing_or_source_absent(tmp_path):
    root = _vault(tmp_path)
    for ev in (None,
               {"quote": "invented words here", "source": "bots/proj/briefings/sessions/s1.md"},
               {"quote": "never retry on 4xx, only on 5xx", "source": "bots/proj/briefings/sessions/nope.md"},
               {"quote": "never retry on 4xx, only on 5xx", "source": "../../etc/passwd"}):
        p = evidence.verify_page(_page(evidence=ev), root)
        assert p.type == "reference" and p.confidence == "inferred" and p.unverified_feedback
        assert p.evidence is None


def test_unverified_when_quote_cites_a_briefing_that_is_not_a_source(tmp_path):
    """A page may only inherit verification from a briefing it was built from."""
    root = _vault(tmp_path)
    p = evidence.verify_page(_page(
        source_files=["bots/other/briefings/sessions/s2.md"],
        evidence={"quote": "never retry on 4xx, only on 5xx",
                  "source": "bots/proj/briefings/sessions/s1.md"}), root)
    assert p.type == "reference" and p.unverified_feedback


def test_non_feedback_types_pass_through_untouched(tmp_path):
    root = _vault(tmp_path)
    p = evidence.verify_page(_page(type="reference"), root)
    assert p.type == "reference" and not p.unverified_feedback


def test_unverified_page_routes_to_inbox_even_single_source(tmp_path):
    root = _vault(tmp_path)
    p = evidence.verify_page(_page(evidence=None), root)
    assert _target_path_for_page(p, root) == root / "shared" / "_inbox" / "reference" / "retry-5xx-only.md"


def test_unverified_page_never_universally_promotes(tmp_path):
    root = _vault(tmp_path)
    state = ExtractionState(last_run=None)
    p = evidence.verify_page(_page(evidence=None, source_files=[
        "bots/proj/briefings/sessions/s1.md", "bots/other/briefings/sessions/s2.md"]), root)
    apply_pages([p], state, root, run_id="r1")
    assert (root / "shared" / "_inbox" / "reference" / "retry-5xx-only.md").exists()
    assert not (root / "shared" / "reference" / "retry-5xx-only.md").exists()
    assert state.entries["reference/retry-5xx-only"].status == "inbox"


def test_verified_single_source_page_auto_promotes(tmp_path):
    root = _vault(tmp_path)
    state = ExtractionState(last_run=None)
    p = evidence.verify_page(_page(evidence={"quote": "never retry on 4xx, only on 5xx",
                                             "source": "bots/proj/briefings/sessions/s1.md"}), root)
    apply_pages([p], state, root, run_id="r1")
    out = root / "shared" / "feedback" / "retry-5xx-only.md"
    assert out.exists()
    assert "confidence: verified" in out.read_text()
