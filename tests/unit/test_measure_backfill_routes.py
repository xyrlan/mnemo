"""``tools/measure_backfill_routes.py``: where would the normal gates send backfill pages? (#471)

Over synthetic pages only. What these pin: each route is the real routing
code's answer with only the origin stamp taken off, the sample is the live
route and nothing else, a rater sees nothing that names the route or the
origin, the bar is the declared one, the budget is the issue's, and neither
the vault nor any page in it is written.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from mnemo.core.text_utils import GRAPH_SECTION_MARKER

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_backfill_routes.py"
_spec = importlib.util.spec_from_file_location("measure_backfill_routes", _TOOL)
mb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mb)


def _staged(vault: Path, slug: str, *, extra: str = "", page_type: str = "reference",
            sources: int = 1, origin: bool = True, body: str = "The rule.",
            name: str = "") -> Path:
    path = vault / "shared" / "_inbox" / page_type / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    src = "".join("  - bots/demo/memory/x%d.md\n" % i for i in range(sources))
    stamp = "origin: backfill\n" if origin else ""
    path.write_text(
        f"---\nname: {name or slug + ' name'}\nslug: {slug}\ndescription: d\ntype: {page_type}\n"
        f"{extra}{stamp}sources:\n{src}---\n\n{body}\n"
        f"\n{GRAPH_SECTION_MARKER}\n## Sources\n- [[bots/demo/x0]]\n",
        encoding="utf-8",
    )
    return path


INFERRED = "confidence: inferred\n"


def _routes(vault: Path) -> dict:
    return {r["id"]: (r["route"], r["reason"]) for r in mb.population(vault)}


def test_each_page_takes_the_route_the_normal_gates_give_it(tmp_vault: Path):
    _staged(tmp_vault, "sys", extra=INFERRED + "reference_gate: system\n")
    _staged(tmp_vault, "tech", extra=INFERRED + "reference_gate: technique\n")
    _staged(tmp_vault, "gen", extra=INFERRED + "reference_gate: generic\n")
    _staged(tmp_vault, "narr", extra=INFERRED + "reference_gate: narrative\n")
    _staged(tmp_vault, "unanswered", extra=INFERRED)
    _staged(tmp_vault, "two-src", extra=INFERRED + "reference_gate: system\n", sources=2)
    _staged(tmp_vault, "demoted",
            extra=INFERRED + "demoted_from: feedback\nreference_gate: system\n")
    _staged(tmp_vault, "alpha__deploy", page_type="project")
    assert _routes(tmp_vault) == {
        "reference/sys": ("live", "reference gate: system"),
        "reference/tech": ("live", "reference gate: technique"),
        "reference/gen": ("staged", "reference gate: generic"),
        "reference/narr": ("staged", "reference gate: narrative"),
        "reference/unanswered": ("staged", "reference gate: no answer"),
        "reference/two-src": ("staged", "multi-source (2)"),
        "reference/demoted": ("staged", "evidence gate: no user quote"),
        "project/alpha__deploy": ("live", "project: no gate"),
    }


def test_already_staged_does_not_keep_a_page_staged(tmp_vault: Path):
    """The reference gate keeps a page that is already in _inbox staged.

    Every page here is in _inbox only because of the stamp, so the route is
    asked against an empty root — or no page would ever route live.
    """
    _staged(tmp_vault, "sys", extra=INFERRED + "reference_gate: system\n")
    assert _routes(tmp_vault)["reference/sys"][0] == "live"


def test_pages_without_the_origin_stamp_are_not_the_population(tmp_vault: Path):
    _staged(tmp_vault, "live-capture", extra=INFERRED + "reference_gate: generic\n",
            origin=False)
    _staged(tmp_vault, "bf", extra=INFERRED + "reference_gate: system\n")
    sib = tmp_vault / "shared" / "_inbox" / "reference" / "bf.proposed.md"
    sib.write_text("---\nname: x\norigin: backfill\n---\nx\n", encoding="utf-8")
    assert list(_routes(tmp_vault)) == ["reference/bf"]


def test_the_sample_is_the_live_route_and_a_rater_sees_no_route(tmp_vault: Path):
    _staged(tmp_vault, "sys", extra=INFERRED + "reference_gate: system\n",
            name="Retry the socket", body="Body text here.")
    _staged(tmp_vault, "gen", extra=INFERRED + "reference_gate: generic\n")
    _staged(tmp_vault, "alpha__deploy", page_type="project")
    rows = mb.population(tmp_vault)
    sample = mb.draw(rows)
    assert [r["id"] for r in sample] == ["project/alpha__deploy", "reference/sys"]
    seen = mb.dk.rater_prompt(sample)
    assert "Retry the socket. Body text here." in seen
    for leak in ("backfill", "origin", "system", "reference_gate", "project", "live",
                 "Sources", "bots/demo"):
        assert leak not in seen


def test_route_counts_are_per_type_and_reason():
    rows = [
        {"type": "project", "route": "live", "reason": "project: no gate"},
        {"type": "project", "route": "live", "reason": "project: no gate"},
        {"type": "reference", "route": "staged", "reason": "multi-source (2)"},
    ]
    assert mb.route_counts(rows) == {
        "project": {"live: project: no gate": 2},
        "reference": {"staged: multi-source (2)": 1},
    }


def test_the_bar_budget_and_raters_are_the_declared_ones():
    assert mb.BAR == 0.85
    assert mb.MAX_CALLS == 30
    assert mb.dk.RATERS == ("claude-opus-5-5", "claude-fable-5-1")


def _labelled(a_letters, b_letters, types):
    ids = ["p%d" % i for i in range(len(a_letters))]
    sample = [{"id": i, "text": "t", "type": t,
               "reason": "project: no gate" if t == "project" else "reference gate: system"}
              for i, t in zip(ids, types)]
    cols = ["A@x", "B@x"]
    return sample, {"A@x": dict(zip(ids, a_letters)), "B@x": dict(zip(ids, b_letters))}, cols


def test_report_verdict_reads_both_raters_and_splits_by_route():
    a = ["S"] * 17 + ["G"] * 3
    types = ["project"] * 10 + ["reference"] * 10
    sample, labels, cols = _labelled(a, list(a), types)
    r = mb.report(sample, labels, cols)
    assert r["both_good"]["k"] == 17 and r["verdict"].startswith("PASS")
    assert "normal gates" in r["verdict"]
    assert r["by_route"]["project (project: no gate)"]["k"] == 10
    assert r["by_route"]["reference (reference gate: system)"]["k"] == 7
    b = ["S"] * 16 + ["N"] + ["G"] * 3
    sample, labels, cols = _labelled(a, b, types)
    r = mb.report(sample, labels, cols)
    assert r["both_good"]["k"] == 16 and r["verdict"].startswith("FAIL")
    assert "routing does not change" in r["verdict"]
    assert [row["gate"] for row in r["not_both_good"]] == ["reference"] * 4
    lines = mb.report_lines(r, {"project": {"live: project: no gate": 10}})
    assert any(line.startswith("verdict: FAIL") for line in lines)


def test_freeze_writes_only_under_out_and_never_redraws_over_labels(tmp_vault: Path, tmp_path: Path):
    import pytest

    pages = [_staged(tmp_vault, "p%02d" % i, extra=INFERRED + "reference_gate: system\n")
             for i in range(5)]
    before = {p: p.read_bytes() for p in tmp_vault.rglob("*") if p.is_file()}
    out = tmp_path / "out"
    frozen = mb.freeze(out, tmp_vault, {})
    assert frozen["population_size"] == 5 and len(frozen["sample"]) == 5
    assert frozen["routes"] == {"reference": {"live: reference gate: system": 5}}
    _staged(tmp_vault, "late", extra=INFERRED + "reference_gate: system\n")
    assert mb.freeze(out, tmp_vault, {}) == frozen
    (out / mb.SAMPLE_NAME).unlink()
    with pytest.raises(SystemExit):
        mb.freeze(out, tmp_vault, {"labels": {"A@x": {"p": "S"}}})
    after = {p: p.read_bytes() for p in tmp_vault.rglob("*") if p.is_file()}
    assert {p: after[p] for p in before} == before
    assert set(after) - set(before) == {tmp_vault / "shared/_inbox/reference/late.md"}
    assert pages
