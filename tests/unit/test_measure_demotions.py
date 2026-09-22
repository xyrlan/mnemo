"""``tools/measure_demotions.py``: the reference judge's view of the evidence gate's demotions.

Over synthetic pages only. The numbers the tool prints come from the real
vault; what these pin is that it samples the right pages, shows the judge the
text extraction would show it, and counts the answers the stage's way.
"""
from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

from mnemo.core.extract import reference_gate as gate
from mnemo.core.text_utils import GRAPH_SECTION_MARKER

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_demotions.py"
_spec = importlib.util.spec_from_file_location("measure_demotions", _TOOL)
md = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(md)


def _staged(vault: Path, slug: str, *, extra: str = "", age_days: float = 1.0,
            body: str = "The rule.") -> Path:
    path = vault / "shared" / "_inbox" / "reference" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {slug} name\nslug: {slug}\ndescription: d\ntype: reference\n{extra}"
        f"sources:\n  - bots/demo/x.md\n---\n\n{body}\n"
        f"\n{GRAPH_SECTION_MARKER}\n## Sources\n- [[bots/demo/x]]\n",
        encoding="utf-8",
    )
    ts = time.time() - age_days * 86400 - 60
    os.utime(path, (ts, ts))
    return path


def test_the_sample_is_the_demotions_and_nothing_else(tmp_vault: Path):
    _staged(tmp_vault, "demoted", extra="demoted_from: feedback\n")
    _staged(tmp_vault, "held", extra="reference_gate: generic\n")
    _staged(tmp_vault, "plain")

    assert [r["id"] for r in md.build_sample(tmp_vault)] == ["reference/demoted"]


def test_the_judge_reads_what_extraction_would_hand_it(tmp_vault: Path):
    """Name and body, no Sources section: the graph links are for humans, and a
    judge reading them would be grading a different text from the stage's."""
    _staged(tmp_vault, "demoted", extra="demoted_from: feedback\n", body="Use X for Y.")

    (row,) = md.build_sample(tmp_vault)

    assert row["text"] == gate.view("demoted name", "Use X for Y.")
    assert "Sources" not in row["text"]
    assert row["projects"] == ["demo"]


def test_no_answer_counts_as_held_the_stages_failure_direction():
    assert md.would_hold(None, gate.KEEP)
    assert md.would_hold("", gate.KEEP)
    assert md.would_hold("G", gate.KEEP) and md.would_hold("N", gate.KEEP)
    assert not md.would_hold("T", gate.KEEP) and not md.would_hold("S", gate.KEEP)
    assert md.tally({"a": None, "b": "G", "c": "G"}, ["a", "b", "c"]) == {"none": 1, "G": 2}


def test_the_report_counts_held_old_and_kept_from_answered_rows_only():
    now = 1_000 * 86400.0
    sample = [
        {"id": "reference/a", "text": "a", "projects": ["p"], "mtime": now - 20 * 86400},
        {"id": "reference/b", "text": "b", "projects": ["p"], "mtime": now - 2 * 86400},
        {"id": "reference/c", "text": "c is specific", "projects": ["q"], "mtime": now},
        {"id": "reference/d", "text": "d", "projects": ["q"], "mtime": now},  # not answered
    ]
    verdicts = {"reference/a": "G", "reference/b": "N", "reference/c": "T"}

    text = "\n".join(md.report_lines(sample, verdicts, gate.KEEP, days=14, now=now))

    assert "answered 3 of 4" in text
    assert "would hold (G/N/no answer): 2 of 3" in text
    assert "would archive if demotions were in scope: 1" in text
    assert "T reference/c" in text


def test_the_report_says_so_when_nothing_is_answered():
    assert md.report_lines([{"id": "x", "text": "", "projects": [], "mtime": 0}], {},
                           gate.KEEP, days=14, now=0) == ["no answers yet — run with --score"]
