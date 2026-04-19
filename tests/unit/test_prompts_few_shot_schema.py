"""Few-shot schema regression test — PR F1 of the v0.9 refactor roadmap.

Safety net for the upcoming ``prompts/`` package split (PR F2). Each few-shot
example's ``Output:`` JSON must round-trip cleanly through the production
``_parse_pages_from_response`` filter — the same sanitization gate that
consumes real LLM output. If F2 moves the templates into sub-modules and
anything drifts (a trailing whitespace, a smart quote, a field silently
dropped), this test catches it before F2 merges.

Why round-trip vs. a Pydantic schema: there is no Pydantic model for
``ExtractedPage`` coming from the LLM. ``_parse_pages_from_response`` IS
the schema — it rejects pages with empty slug/body/source_files. Validating
the few-shots against it is the strongest guarantee the examples still
calibrate the model correctly.
"""
from __future__ import annotations

import re

import pytest

from mnemo.core.extract import _parse_pages_from_response, prompts


# Each few-shot is a multi-example string. Every example emits its Output
# JSON on a single line starting with {"pages":[ — grab each line and
# round-trip it through the production parser.
_OUTPUT_JSON_RE = re.compile(r'^(\{"pages":\s*\[.*\]\})\s*$', re.MULTILINE)


def _extract_output_blobs(few_shot: str) -> list[str]:
    return _OUTPUT_JSON_RE.findall(few_shot)


_CASES: list[tuple[str, str, str]] = [
    ("_FEW_SHOT_FEEDBACK",  prompts._FEW_SHOT_FEEDBACK,  "feedback"),
    ("_FEW_SHOT_USER",      prompts._FEW_SHOT_USER,      "user"),
    ("_FEW_SHOT_REFERENCE", prompts._FEW_SHOT_REFERENCE, "reference"),
]


_PARAMS = [
    (shot_name, idx, blob, default_type)
    for shot_name, body, default_type in _CASES
    for idx, blob in enumerate(_extract_output_blobs(body))
]


@pytest.mark.parametrize(
    ("shot_name", "blob_index", "blob", "default_type"),
    _PARAMS,
    ids=[f"{name}#{idx}" for name, idx, _, _ in _PARAMS],
)
def test_few_shot_output_round_trips_cleanly(
    shot_name: str, blob_index: int, blob: str, default_type: str
) -> None:
    """Each Output JSON in a few-shot must survive `_parse_pages_from_response`."""
    pages = _parse_pages_from_response(blob, default_type)
    assert pages, (
        f"{shot_name}[#{blob_index}] produced zero pages — "
        "_parse_pages_from_response rejected the example. Drift between "
        "few-shot schema and production filter."
    )
    for i, page in enumerate(pages):
        assert page.slug, f"{shot_name}[#{blob_index}] page {i}: empty slug"
        assert page.body.strip(), (
            f"{shot_name}[#{blob_index}] page {i}: empty body"
        )
        assert page.source_files, (
            f"{shot_name}[#{blob_index}] page {i}: empty source_files"
        )


def test_every_few_shot_has_at_least_one_output() -> None:
    """Belt-and-braces: if PR F2 refactors the few-shots into a shape the
    regex no longer catches, the per-example test above would report
    "zero blobs, zero assertions" and masquerade as a green run. This
    guards against that.
    """
    for name, body, _ in _CASES:
        blobs = _extract_output_blobs(body)
        assert blobs, (
            f"{name}: regex found zero Output JSON blobs — "
            "shape drift between few-shots and the regex used here?"
        )
