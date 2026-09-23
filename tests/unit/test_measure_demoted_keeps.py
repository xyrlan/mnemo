"""``tools/measure_demoted_keeps.py``: do the gate's S/T keeps hold on demoted pages? (#465)

Over synthetic pages only. What these pin: the population is exactly the
frontmatter the issue names, the draw is reproducible, a rater sees nothing
that names the gate or the demotion, the budget holds across reruns, the
statistics are right, the bar is the declared one, and no page is written.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from mnemo.core.text_utils import GRAPH_SECTION_MARKER

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_demoted_keeps.py"
_spec = importlib.util.spec_from_file_location("measure_demoted_keeps", _TOOL)
mk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mk)


def _staged(vault: Path, slug: str, *, extra: str = "", page_type: str = "reference",
            body: str = "The rule.", name: str = "") -> Path:
    path = vault / "shared" / "_inbox" / page_type / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name or slug + ' name'}\nslug: {slug}\ndescription: d\ntype: {page_type}\n"
        f"{extra}sources:\n  - bots/demo/x.md\n---\n\n{body}\n"
        f"\n{GRAPH_SECTION_MARKER}\n## Sources\n- [[bots/demo/x]]\n",
        encoding="utf-8",
    )
    return path


DEMOTED_S = "confidence: inferred\ndemoted_from: feedback\nreference_gate: system\n"
DEMOTED_T = "demoted_from: feedback\nreference_gate: technique\n"


def test_population_is_demoted_feedback_judged_system_or_technique(tmp_vault: Path):
    _staged(tmp_vault, "keep-s", extra=DEMOTED_S)
    _staged(tmp_vault, "keep-t", extra=DEMOTED_T)
    _staged(tmp_vault, "generic", extra="demoted_from: feedback\nreference_gate: generic\n")
    _staged(tmp_vault, "narrative", extra="demoted_from: feedback\nreference_gate: narrative\n")
    _staged(tmp_vault, "unjudged", extra="demoted_from: feedback\n")
    _staged(tmp_vault, "not-demoted", extra="reference_gate: system\n")
    _staged(tmp_vault, "other-demotion", extra="demoted_from: project\nreference_gate: system\n")
    # A staged rewrite sibling is not a plain page.
    sib = tmp_vault / "shared" / "_inbox" / "reference" / "keep-s.proposed.md"
    sib.write_text("---\nname: x\ndemoted_from: feedback\nreference_gate: system\n---\nx\n",
                   encoding="utf-8")
    # An archive dir under _inbox is not a page type.
    arch = tmp_vault / "shared" / "_inbox" / "rejected-1" / "old.md"
    arch.parent.mkdir(parents=True)
    arch.write_text("---\nname: x\ndemoted_from: feedback\nreference_gate: system\n---\nx\n",
                    encoding="utf-8")

    rows = mk.population(tmp_vault)
    assert [r["id"] for r in rows] == ["reference/keep-s", "reference/keep-t"]
    assert [r["gate"] for r in rows] == ["system", "technique"]


def test_a_rater_sees_name_and_body_and_nothing_that_names_the_gate(tmp_vault: Path):
    _staged(tmp_vault, "page", extra=DEMOTED_S, name="Retry the socket", body="Body text here.")
    (row,) = mk.population(tmp_vault)
    seen = mk.rater_prompt([row])
    assert "Retry the socket. Body text here." in seen
    for leak in ("system", "demoted", "reference_gate", "feedback", "Sources", "bots/demo"):
        assert leak not in seen
    assert "gate" not in mk.rater_system().lower()
    assert "demot" not in mk.rater_system().lower()


def test_the_raters_use_the_gates_definitions_and_are_not_the_gates_model():
    from mnemo.core import config
    from mnemo.core.extract import reference_gate as gate

    system = mk.rater_system()
    for text in gate.CATEGORIES.values():
        assert text in system
    assert config.DEFAULTS["extraction"]["referenceGate"]["model"] not in mk.RATERS
    assert len(set(mk.RATERS)) == 2


def test_draw_is_uniform_reproducible_and_order_independent():
    pop = [{"id": "reference/p%02d" % i, "text": "t"} for i in range(67)]
    one = mk.draw(pop)
    assert len(one) == mk.SAMPLE_SIZE == 40
    assert len({r["id"] for r in one}) == 40
    assert mk.draw(list(reversed(pop))) == one
    assert [r["id"] for r in mk.draw(pop, seed=1)] != [r["id"] for r in one]
    assert mk.draw(pop[:5]) == pop[:5]


def test_the_second_rater_reads_in_reverse_and_batches_skip_done():
    sample = [{"id": "p%d" % i, "text": "t"} for i in range(25)]
    assert [len(b) for b in mk.batches(0, sample, {})] == [10, 10, 5]
    assert mk.batches(1, sample, {})[0][0]["id"] == "p24"
    done = {"p%d" % i: "S" for i in range(20)}
    assert [r["id"] for b in mk.batches(0, sample, done) for r in b] == ["p%d" % i for i in range(20, 25)]
    todo = mk.plan(sample, {})
    assert [m for m, _ in todo] == [mk.RATERS[0]] * 3 + [mk.RATERS[1]] * 3


def test_parse_labels_is_strict_about_content():
    batch = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    text = ('```json\n{"labels": [{"i": 1, "cat": "s"}, {"i": 2, "cat": "X"}, '
            '{"i": 9, "cat": "G"}, {"i": true, "cat": "G"}, {"i": 3, "cat": "W"}]}\n```')
    assert mk.parse_labels(text, batch) == {"a": "S", "c": "W"}
    assert mk.parse_labels("no json", batch) == {}
    assert mk.parse_labels('{"labels": 3}', batch) == {}


def test_send_saves_after_every_call_and_the_budget_holds_across_reruns():
    sample = [{"id": "p%d" % i, "text": "t%d" % i} for i in range(40)]
    store = {"calls": 0, "usd": 0.0, "labels": {}}
    saved = []
    asked = []

    def ask(prompt, system, model):
        asked.append(model)
        n = prompt.count("\n\n[")
        return json.dumps({"labels": [{"i": i, "cat": "S"} for i in range(1, n + 1)]}), 0.1

    log = mk.send(mk.plan(sample, {}), store, ask, lambda s: saved.append(json.dumps(s)),
                  max_calls=5)
    assert store["calls"] == 5 and len(saved) == 5 and len(asked) == 5
    assert "budget spent" in log[-1]
    # A rerun resumes: only what is missing, and never past the budget.
    rest = mk.plan(sample, store["labels"])
    assert sum(len(b) for _, b in rest) == 80 - 50
    mk.send(rest, store, ask, lambda s: None, max_calls=5)
    assert store["calls"] == 5
    mk.send(rest, store, ask, lambda s: None, max_calls=mk.MAX_CALLS)
    assert store["calls"] == 8
    assert mk.plan(sample, store["labels"]) == []


def test_estimate_counts_calls_and_prices_each_rater():
    sample = [{"id": "p%d" % i, "text": "x" * 400} for i in range(40)]
    e = mk.estimate(mk.plan(sample, {}))
    assert e["calls"] == 8 and e["pages"] == 80
    assert e["calls"] <= mk.MAX_CALLS
    assert e["usd"] > 0


def test_wilson_and_kappa():
    lo, hi = mk.wilson(34, 40)
    assert lo == pytest.approx(0.7091, abs=1e-3) and hi == pytest.approx(0.9294, abs=1e-3)
    assert mk.wilson(0, 0) == (None, None)
    G, J = mk.GOOD, mk.JUNK
    assert mk.kappa([G, J, G, J], [G, J, G, J]) == pytest.approx(1.0)
    # observed .5, expected .5 -> 0
    assert mk.kappa([G, G, J, J], [G, J, G, J]) == pytest.approx(0.0)
    assert mk.kappa([G, G], [G, G]) is None
    assert mk.kappa([], []) is None


def _labels(a_letters, b_letters):
    ids = ["p%d" % i for i in range(len(a_letters))]
    sample = [{"id": i, "text": "t", "gate": "system" if n % 2 else "technique"}
              for n, i in enumerate(ids)]
    cols = ["A@x", "B@x"]
    return sample, {"A@x": dict(zip(ids, a_letters)), "B@x": dict(zip(ids, b_letters))}, cols


def test_the_bar_is_the_declared_one_and_reads_both_raters():
    assert mk.BAR == 0.85
    # 34/40 good under both = 85% exactly: passes.
    a = ["S"] * 34 + ["G"] * 6
    sample, labels, cols = _labels(a, list(a))
    r = mk.report(sample, labels, cols)
    assert r["both_good"]["k"] == 34 and r["verdict"].startswith("PASS")
    # Each rater alone at 85%, but on different pages: both-good 80% fails.
    b = ["S"] * 32 + ["N"] * 2 + ["S"] * 2 + ["G"] * 4
    sample, labels, cols = _labels(a, b)
    r = mk.report(sample, labels, cols)
    assert r["raters"]["A@x"]["k"] == 34 and r["raters"]["B@x"]["k"] == 34
    assert r["both_good"]["k"] == 32 and r["verdict"].startswith("FAIL")
    assert r["raters"]["B@x"]["cats"] == {"S": 34, "N": 2, "G": 4}
    assert len(r["not_both_good"]) == 8
    # W counts as junk.
    sample, labels, cols = _labels(["W"] * 40, ["S"] * 40)
    assert mk.report(sample, labels, cols)["both_good"]["k"] == 0


def test_no_verdict_until_both_raters_labelled_every_page():
    sample, labels, cols = _labels(["S"] * 40, ["S"] * 40)
    del labels["B@x"]["p3"]
    r = mk.report(sample, labels, cols)
    assert r["verdict"].startswith("incomplete") and not r["complete"]


def test_by_gate_splits_both_good_by_the_hidden_verdict():
    sample, labels, cols = _labels(["S", "S", "G", "S"], ["S", "S", "S", "G"])
    r = mk.report(sample, labels, cols)
    assert r["by_gate"]["technique"]["k"] == 1 and r["by_gate"]["technique"]["n"] == 2
    assert r["by_gate"]["system"]["k"] == 1 and r["by_gate"]["system"]["n"] == 2
    assert any("verdict:" in line for line in mk.report_lines(dict(r, population=4)))


def test_freeze_draws_once_refuses_a_redraw_over_labels_and_writes_no_page(tmp_vault: Path):
    pages = [_staged(tmp_vault, "p%02d" % i, extra=DEMOTED_S) for i in range(50)]
    before = {p: p.read_bytes() for p in pages}
    out = tmp_vault / ".mnemo" / mk.OUT_DIR
    frozen = mk.freeze(out, tmp_vault, {})
    assert frozen["population_size"] == 50 and len(frozen["sample"]) == 40
    _staged(tmp_vault, "late", extra=DEMOTED_S)
    assert mk.freeze(out, tmp_vault, {}) == frozen
    (out / mk.SAMPLE_NAME).unlink()
    with pytest.raises(SystemExit):
        mk.freeze(out, tmp_vault, {"labels": {"A@x": {"p": "S"}}})
    assert {p: p.read_bytes() for p in pages} == before
    written = {p.relative_to(tmp_vault) for p in tmp_vault.rglob("*") if p.is_file()}
    assert all(str(p).startswith(".mnemo") or str(p).startswith("shared/_inbox")
               or p.name == "mnemo.config.json" for p in written)
