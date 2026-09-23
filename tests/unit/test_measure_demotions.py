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


# --- --stamp (#432) ----------------------------------------------------------

def _sample(*slugs: str) -> list:
    return [{"id": "reference/" + s, "text": s, "projects": [], "mtime": 0} for s in slugs]


def test_stamp_without_apply_writes_nothing(tmp_vault: Path):
    page = _staged(tmp_vault, "a", extra="demoted_from: feedback\n")
    before, mtime = page.read_bytes(), page.stat().st_mtime

    todo, skipped = md.plan_stamps(tmp_vault, _sample("a"), {"reference/a": "G"}, gate.LABELS)
    text = "\n".join(md.stamp_lines(todo, skipped, applied=None))

    assert "would stamp 1 staged demotions: generic 1" in text
    assert "dry run" in text
    assert page.read_bytes() == before and page.stat().st_mtime == mtime


def test_stamp_apply_writes_exactly_the_missing_lines(tmp_vault: Path):
    a = _staged(tmp_vault, "a", extra="demoted_from: feedback\n")
    b = _staged(tmp_vault, "b", extra="demoted_from: feedback\n")
    done = _staged(tmp_vault, "done", extra="demoted_from: feedback\nreference_gate: system\n")
    unanswered = _staged(tmp_vault, "unanswered", extra="demoted_from: feedback\n")
    untouched = {p: p.read_bytes() for p in (done, unanswered)}
    before_a = a.read_bytes()
    verdicts = {"reference/a": "N", "reference/b": "T", "reference/done": "G",
                "reference/gone": "G", "reference/unanswered": None}

    todo, skipped = md.plan_stamps(
        tmp_vault, _sample("a", "b", "done", "gone", "unanswered"), verdicts, gate.LABELS)
    assert md.apply_stamps(todo) == 2

    # One line, right after demoted_from:, every other byte where it was.
    # `write_text` writes the platform's line ending, so expect that one.
    eol = b"\r\n" if b"\r\n" in before_a else b"\n"
    assert a.read_bytes() == before_a.replace(
        b"demoted_from: feedback" + eol,
        b"demoted_from: feedback" + eol + b"reference_gate: narrative" + eol)
    assert b"reference_gate: technique" + eol in b.read_bytes()
    assert all(p.read_bytes() == raw for p, raw in untouched.items())
    assert skipped == {"gone": 1, "no answer": 1, "not a demotion or already stamped": 1}
    # What the stamp means is what the expiry reads.
    from mnemo.core import inbox
    assert [p.slug for p in inbox.staged_pages(tmp_vault) if p.gate_held] == ["a"]
    # Idempotent: a second run finds nothing missing.
    assert md.plan_stamps(tmp_vault, _sample("a", "b"), verdicts, gate.LABELS)[0] == []


def test_stamp_keeps_a_crlf_file_crlf():
    raw = b"---\r\nname: x\r\ndemoted_from: feedback\r\n---\r\n\r\nbody\r\n"
    assert md._stamped(raw, "generic") == raw.replace(
        b"feedback\r\n", b"feedback\r\nreference_gate: generic\r\n")


def test_stamp_leaves_a_page_that_is_no_longer_a_demotion():
    assert md._stamped(b"---\nname: x\n---\n\ndemoted_from: in the body\n", "generic") is None
    assert md._stamped(b"no frontmatter\n", "generic") is None


def test_the_stamp_takes_the_files_own_line_ending(tmp_vault: Path):
    """CRLF on every platform, not only on Windows: a vault edited there keeps
    CRLF when synced back, and a bare LF line would be the one odd byte."""
    path = tmp_vault / "shared" / "_inbox" / "reference" / "crlf.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"---\r\nname: crlf\r\ndemoted_from: feedback\r\nsources:\r\n---\r\n\r\nbody\r\n")

    todo, _ = md.plan_stamps(tmp_vault, _sample("crlf"), {"reference/crlf": "S"}, gate.LABELS)
    assert md.apply_stamps(todo) == 1

    assert path.read_bytes() == (
        b"---\r\nname: crlf\r\ndemoted_from: feedback\r\nreference_gate: system\r\n"
        b"sources:\r\n---\r\n\r\nbody\r\n")
