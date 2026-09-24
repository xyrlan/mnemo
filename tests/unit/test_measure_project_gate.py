"""``tools/measure_project_gate.py``: what would the reference gate do with backfill project pages? (#485)

Over synthetic corpora only. What these pin: the gate asked is the real
``judge_pages`` with the shipped prompt and it reads exactly the text the
raters read, only project rows are taken, a failed reply is asked again and
counted as held until then, the rate is over what the gate lets through, the
bar is #471's, W pages are counted as the ceiling, the budget is the issue's,
and the corpus files are never written.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from mnemo.core.extract import reference_gate

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_project_gate.py"
_spec = importlib.util.spec_from_file_location("measure_project_gate", _TOOL)
mp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mp)
dk = mp.dk

COLS = [dk.column(m) for m in dk.RATERS]


def _row(i: int, page_type: str = "project", name: str = "") -> dict:
    name = name or "page-%02d" % i
    return {"id": "%s/demo__%s" % (page_type, name), "type": page_type, "route": "live",
            "reason": "project: no gate",
            "text": reference_gate.view(name, "# Title\n\nBody %d with   spaces." % i)}


def _corpus(tmp_path: Path, rows: list, labels: dict) -> Path:
    d = tmp_path / "corpus"
    d.mkdir()
    (d / "sample.json").write_text(json.dumps({"sample": rows}), encoding="utf-8")
    (d / "labels.json").write_text(json.dumps({"calls": 0, "labels": labels}), encoding="utf-8")
    return d


def _labels(pairs: dict) -> dict:
    """``{id: "AB"}`` -> both rater columns."""
    return {COLS[0]: {i: ab[0] for i, ab in pairs.items()},
            COLS[1]: {i: ab[1] for i, ab in pairs.items()}}


def test_the_gate_reads_exactly_what_the_raters_read():
    long_body = "word " * 600
    for name, body in [("slug-name", "Body."), ("v1.2 release", "x. y. z"),
                       ("n", long_body), ("ends.", "b")]:
        text = reference_gate.view(name, body)
        page = mp.as_page({"id": "project/x", "text": text})
        assert reference_gate.view(page.name, page.body) == text
        assert reference_gate.needs_judging(page)


def test_only_project_rows_are_taken_and_the_corpus_is_not_written(tmp_path: Path):
    rows = [_row(1), _row(2, "reference"), _row(3)]
    d = _corpus(tmp_path, rows, _labels({rows[0]["id"]: "SS"}))
    before = {p.name: p.read_bytes() for p in d.iterdir()}
    got, labels = mp.project_rows(d)
    assert [r["id"] for r in got] == [rows[0]["id"], rows[2]["id"]]
    assert labels[COLS[0]] == {rows[0]["id"]: "S"}
    assert {p.name: p.read_bytes() for p in d.iterdir()} == before


def test_judge_is_the_real_judge_pages_and_a_bad_reply_is_no_answer():
    batch = [_row(1), _row(2)]
    seen = []

    def ask(prompt):
        seen.append(prompt)
        return '{"verdicts": [{"i": 1, "cat": "N"}, {"i": 2, "cat": "S"}]}'

    assert mp.judge(batch, ask) == {batch[0]["id"]: "N", batch[1]["id"]: "S"}
    assert seen == [reference_gate.build_prompt([r["text"] for r in batch])]
    assert mp.judge(batch, lambda p: "sorry") == {batch[0]["id"]: "", batch[1]["id"]: ""}


def test_pending_batches_by_ten_and_asks_a_failed_reply_again():
    rows = [_row(i) for i in range(23)]
    assert [len(b) for b in mp.pending(rows, {})] == [10, 10, 3]
    done = {r["id"]: "S" for r in rows}
    done[rows[4]["id"]] = ""
    assert [[r["id"] for r in b] for b in mp.pending(rows, done)] == [[rows[4]["id"]]]


def test_report_rates_what_the_gate_lets_through_against_471s_bar():
    rows = [_row(i) for i in range(10)]
    ids = [r["id"] for r in rows]
    labels = _labels({ids[0]: "SS", ids[1]: "SS", ids[2]: "ST", ids[3]: "TT", ids[4]: "SS",
                      ids[5]: "SS", ids[6]: "NN", ids[7]: "WS", ids[8]: "SS", ids[9]: "GG"})
    answers = {ids[0]: "S", ids[1]: "S", ids[2]: "T", ids[3]: "S", ids[4]: "S",
               ids[5]: "N", ids[6]: "N", ids[7]: "S", ids[8]: "", ids[9]: "G"}
    data = mp.report(rows, labels, answers, COLS)
    assert mp.BAR == 0.85 and data["bar"] == 0.85
    assert data["no_gate"]["k"] == 7 and data["no_gate"]["n"] == 10
    assert data["gate"] == {"S": 5, "T": 1, "N": 2, "G": 1, "no answer": 1}
    assert (data["passed"]["k"], data["passed"]["n"]) == (5, 6)
    assert data["verdict"].startswith("FAIL: 83.3%")
    # held: two good pages lost (the N'd one and the unanswered one)
    assert data["held"]["n"] == 4 and data["held"]["good"] == 2
    # W: counted, let through, and the ceiling is good / (good + W)
    assert data["wrong"]["n"] == 1 and data["wrong"]["passed"] == 1
    assert (data["wrong"]["ceiling"]["k"], data["wrong"]["ceiling"]["n"]) == (7, 8)
    assert [r["id"] for r in data["junk_passed"]] == [ids[7]]


def test_report_passes_at_the_bar_and_is_incomplete_until_every_page_is_answered():
    rows = [_row(i) for i in range(20)]
    ids = [r["id"] for r in rows]
    labels = _labels({i: ("SS" if k < 17 else "NN") for k, i in enumerate(ids)})
    answers = {i: "S" for i in ids}
    assert mp.report(rows, labels, answers, COLS)["verdict"].startswith("PASS: 85.0%")
    del answers[ids[0]]
    assert mp.report(rows, labels, answers, COLS)["verdict"].startswith("incomplete")


def test_send_uses_the_shipped_gate_and_stops_at_the_budget(tmp_path: Path, monkeypatch):
    rows = [_row(i) for i in range(12)]
    d = _corpus(tmp_path, rows, _labels({r["id"]: "SS" for r in rows}))
    before = {p.name: p.read_bytes() for p in d.iterdir()}
    calls = []

    def provider(prompt, *, system, model, timeout):
        calls.append((system, model))
        n = prompt.count("\n\n[")
        return SimpleNamespace(text=json.dumps({"verdicts": [
            {"i": k, "cat": "S"} for k in range(1, n + 1)]}), total_cost_usd=0.01)

    from mnemo.core import config, llm
    monkeypatch.setattr(llm, "resolve", lambda cfg: provider)
    cfg = config.load_config()
    out = tmp_path / "out"
    monkeypatch.setattr(mp, "MAX_CALLS", 1)
    argv = ["--corpus", "clubinho", "--dir", "clubinho=%s" % d, "--out", str(out), "--send"]
    assert mp.main(argv) == 0
    store = json.loads((out / "answers.json").read_text(encoding="utf-8"))
    assert store["calls"] == 1
    assert calls == [(reference_gate.SYSTEM_PROMPT, cfg["extraction"]["referenceGate"]["model"])]
    col = mp.column(calls[0][1], reference_gate.SYSTEM_PROMPT)
    assert len(store["answers"]["clubinho"][col]) == 10
    assert {p.name: p.read_bytes() for p in d.iterdir()} == before


def test_the_budget_is_the_issues():
    fresh = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(fresh)
    assert fresh.MAX_CALLS == 15 and fresh.BATCH == 10
