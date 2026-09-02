"""ExtractedPage.evidence/confidence round-trip: LLM JSON → page → frontmatter → parse."""
from __future__ import annotations

import json

from mnemo.core.extract import _parse_pages_from_response
from mnemo.core.extract.inbox.rendering import _render_page
from mnemo.core.extract.inbox.types import ExtractedPage
from mnemo.core.filters import parse_frontmatter


def _page_json(**extra):
    base = {"slug": "retry-5xx-only", "name": "Retry only on 5xx", "description": "d",
            "type": "feedback", "body": "Retry only 5xx.\n\n**Why:** x\n\n**How to apply:** y",
            "source_files": ["bots/proj/briefings/sessions/s1.md"]}
    base.update(extra)
    return json.dumps({"pages": [base]})


def test_parse_reads_evidence_dict_and_defaults_confidence_inferred():
    pages = _parse_pages_from_response(_page_json(
        evidence={"quote": "never retry on 4xx, only on 5xx",
                  "source": "bots/proj/briefings/sessions/s1.md"}), "feedback")
    assert pages[0].evidence == {"quote": "never retry on 4xx, only on 5xx",
                                 "source": "bots/proj/briefings/sessions/s1.md"}
    assert pages[0].confidence == "inferred"
    assert pages[0].unverified_feedback is False


def test_parse_drops_malformed_evidence():
    assert _parse_pages_from_response(_page_json(evidence="a string"), "feedback")[0].evidence is None
    assert _parse_pages_from_response(_page_json(evidence={"quote": ""}), "feedback")[0].evidence is None
    assert _parse_pages_from_response(_page_json(evidence={"quote": "q", "source": 3}), "feedback")[0].evidence is None
    assert _parse_pages_from_response(_page_json(), "feedback")[0].evidence is None


def test_render_writes_confidence_and_nested_evidence_that_parse_back():
    page = ExtractedPage(slug="s", type="feedback", name="N", description="D", body="B",
                         source_files=["bots/p/briefings/sessions/x.md"], source_hash="h",
                         evidence={"quote": 'use "yarn": not npm', "source": "bots/p/briefings/sessions/x.md"},
                         confidence="verified")
    fm = parse_frontmatter(_render_page(page, run_id="r1", auto_promoted=True))
    assert fm["confidence"] == "verified"
    assert fm["evidence"]["quote"] == 'use "yarn": not npm'
    assert fm["evidence"]["source"] == "bots/p/briefings/sessions/x.md"
    assert "demoted_from" not in fm


def test_render_marks_demoted_pages():
    page = ExtractedPage(slug="s", type="reference", name="N", description="D", body="B",
                         source_files=["a.md"], source_hash="h", confidence="inferred",
                         unverified_feedback=True)
    fm = parse_frontmatter(_render_page(page, run_id="r1"))
    assert fm["confidence"] == "inferred"
    assert fm["demoted_from"] == "feedback"
    assert "evidence" not in fm
