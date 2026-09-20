"""``tools/measure_recall_judged.py`` over a built access log and a stub judge.

No network: the judge is a function the test hands in, so every state and
question it was given can be read back. Pinned here: the pairing must be the
harness's own (latest list in the window that returned the slug), a rule the
judge did not answer for must stay "unjudged" and never count as irrelevant,
the gated order must be ``tools._query_rerank``'s, and a run without
``--send`` must not open a connection.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from mnemo.core.mcp import tools

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_recall_judged.py"
_spec = importlib.util.spec_from_file_location("measure_recall_judged", _TOOL)
mrj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mrj)


def _ts(value: str) -> float:
    return float(value or 0)


def _list(ts, slugs, *, query="fix the ledger race", project="p", topic="data"):
    args = {"topic": topic}
    if query:
        args["query"] = query
    return {"tool": "list_rules_by_topic", "project": project, "timestamp": str(ts),
            "args": args, "hit_slugs": slugs}


def _read(ts, slug, *, project="p"):
    return {"tool": "read_mnemo_rule", "project": project, "timestamp": str(ts),
            "args": {"slug": slug}}


def _units(entries, **kwargs):
    return mrj.units_from_log(entries, window_s=120.0, parse_ts=_ts, **kwargs)


def test_every_read_after_one_list_call_lands_in_one_unit():
    units = _units([_list(100, ["a", "b", "c"]), _read(104, "b"), _read(110, "c")])
    assert len(units) == 1
    assert units[0]["reads"] == ["b", "c"]
    assert units[0]["query"] == "fix the ledger race"


def test_a_read_outside_the_window_or_not_in_the_list_is_not_paired():
    units = _units([_list(100, ["a", "b"]), _read(300, "a"), _read(105, "z")])
    assert units[0]["reads"] == []


def test_a_read_belongs_to_the_latest_list_that_returned_it():
    units = _units([_list(100, ["a"], query="first task"),
                    _list(150, ["a"], query="second task"), _read(160, "a")])
    by_query = {u["query"]: u["reads"] for u in units}
    assert by_query == {"first task": [], "second task": ["a"]}


def test_an_unqueried_list_makes_no_unit_there_is_no_task_to_judge_against():
    assert _units([_list(100, ["a"], query=None), _read(105, "a")]) == []


def test_the_same_query_twice_is_one_unit_and_a_pre_cutover_name_is_resolved():
    units = _units([_list(100, ["Rule A"]), _read(105, "Rule A"),
                    _list(500, ["a"]), _read(505, "a")], name_map={"Rule A": "a"})
    assert len(units) == 1
    assert units[0]["reads"] == ["a"]


def test_the_judge_sees_the_query_as_state_and_one_question_per_rule():
    calls = []

    def client(state, questions):
        calls.append((state, questions))
        return {"answers": {k: {"type": "noul", "noul": 0.9} for k in questions},
                "usage": {"input_tokens": 50}}

    scores, tokens = mrj.judge_unit("fix the ledger race", {"a": "lock the ledger", "b": "", "c": "x"}, client)
    assert scores == {"a": 0.9, "c": 0.9}  # an empty body is not judged
    assert tokens == 50
    (state, questions), = calls
    assert state == {"developer_task": "fix the ledger race"}
    assert len(questions) == 2
    assert all(q["type"] == "noul" for q in questions.values())
    assert any("lock the ledger" in q["instructions"] for q in questions.values())


def test_a_big_bucket_is_chunked_and_every_rule_keeps_its_own_answer():
    bodies = {"r%03d" % i: "body %d" % i for i in range(mrj.CHUNK + 5)}
    sizes = []

    def client(state, questions):
        sizes.append(len(questions))
        return {"answers": {k: {"noul": 1.0 if q["instructions"].endswith("body 41") else 0.0}
                            for k, q in questions.items()}}

    scores, _ = mrj.judge_unit("task", bodies, client)
    assert sizes == [mrj.CHUNK, 5]
    assert scores["r041"] == 1.0
    assert sum(scores.values()) == 1.0


def test_a_failed_request_leaves_its_rules_unjudged_not_irrelevant():
    scores, _ = mrj.judge_unit("task", {"a": "x"}, lambda state, questions: {"error": 429})
    assert scores == {}
    result = mrj.evaluate([{"noul": scores, "reads": []}], [["a"]])
    assert result["unjudged"] == 1


def test_rule_text_drops_the_link_section_and_is_bounded():
    text = mrj.rule_text("Lock  the\nledger. " + mrj.GRAPH_SECTION + " ## Sources [[x]]")
    assert text == "Lock the ledger."
    assert len(mrj.rule_text("x" * 5000)) == mrj.BODY_CHARS


def test_gated_order_is_the_shipped_query_rerank():
    base = ["a", "b", "c", "d"]
    scores = {"b": 2.5, "d": 4.0, "c": 0.4}
    for gate in (0.0, 1.0, 3.0):
        shipped = tools._query_rerank([{"slug": s} for s in base], list(scores.items()), min_score=gate)
        assert mrj.gated_order(base, scores, gate) == [m["slug"] for m in shipped]
    assert mrj.gated_order(base, scores, None) == base


def test_ndcg_is_one_for_the_ideal_order_and_falls_when_the_best_rule_sinks():
    gain = {"a": 1.0, "b": 0.5, "c": 0.0}
    assert mrj.ndcg(["a", "b", "c"], gain) == pytest.approx(1.0)
    assert mrj.ndcg(["c", "b", "a"], gain) < mrj.ndcg(["b", "a", "c"], gain) < 1.0
    assert mrj.ndcg(["a"], {}) == 0.0


def test_evaluate_reports_both_ground_truths_side_by_side():
    gain = {"s%d" % i: 0.0 for i in range(8)}
    gain.update({"s0": 0.9, "s6": 0.95, "s7": 0.6})
    unit = {"noul": gain, "reads": ["s1"]}
    order = ["s%d" % i for i in range(8)]
    result = mrj.evaluate([unit], [order])
    assert (result["should_read_top"], result["should_read"]) == (1, 2)  # s6 is buried
    assert (result["read_top"], result["read"]) == (1, 1)  # the read label sees no problem
    assert result["precision"] == pytest.approx(1 / 5)
    better = mrj.evaluate([unit], [["s6", "s0", "s7"] + order[1:6]])
    assert better["ndcg"] > result["ndcg"]
    assert better["should_read_top"] == 2


def test_the_model_is_pinned_not_an_alias():
    assert "latest" not in mrj.MODEL and "preview" not in mrj.MODEL


def _no_network(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("opened a connection without --send")
    monkeypatch.setattr(mrj.urllib.request, "urlopen", boom)


def test_judge_without_send_is_a_dry_run(monkeypatch, tmp_path, capsys):
    _no_network(monkeypatch)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: tmp_path)
    monkeypatch.setattr(mrj, "_vault_units", lambda vault: [
        {"project": "p", "topic": "t", "query": "q", "reads": ["a"]}])
    monkeypatch.setattr(mrj, "_bodies", lambda vault, unit: {"a": "body a", "b": "body b"})
    assert mrj.main(["--judge"]) == 0
    assert "dry run: 1 queries, 2 (query, rule) pairs" in capsys.readouterr().out
    assert not (tmp_path / ".mnemo" / mrj.QRELS_NAME).exists()


def test_send_without_a_key_refuses(monkeypatch, tmp_path, capsys):
    _no_network(monkeypatch)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: tmp_path)
    monkeypatch.setattr(mrj, "_vault_units", lambda vault: [])
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert mrj.main(["--judge", "--send"]) == 1
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err


def test_evaluation_reads_the_saved_judgments_and_calls_nothing(monkeypatch, tmp_path, capsys):
    _no_network(monkeypatch)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: tmp_path)
    (tmp_path / ".mnemo").mkdir()
    units = [{"project": "p", "topic": "t", "query": "q", "reads": ["b"],
              "noul": {"a": 0.1, "b": 0.9}}]
    (tmp_path / ".mnemo" / mrj.QRELS_NAME).write_text(json.dumps({
        "model": mrj.MODEL, "question_version": mrj.QUESTION_VERSION,
        "judged_at": "2026-09-19T00:00:00+00:00", "units": units}), encoding="utf-8")
    monkeypatch.setattr(mrj, "_orders", lambda vault, units, gate, shipped=False: [
        ["b", "a"] if shipped else ["a", "b"]])
    assert mrj.main(["--gates", "1"]) == 0
    out = capsys.readouterr().out
    assert "shipped" in out and "no rerank" in out and "gate 1" in out
    assert "should-read in top5 1/1" in out


def test_judgments_for_an_older_question_are_refused(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: tmp_path)
    (tmp_path / ".mnemo").mkdir()
    (tmp_path / ".mnemo" / mrj.QRELS_NAME).write_text(json.dumps({
        "model": mrj.MODEL, "question_version": mrj.QUESTION_VERSION - 1,
        "judged_at": "x", "units": []}), encoding="utf-8")
    assert mrj.main([]) == 1
    assert "older question" in capsys.readouterr().err


def test_the_tool_and_the_stage_ask_the_same_thing():
    """#401: the ordering measured here is the one ``recall.rerank`` ships."""
    from mnemo.core.mcp import rerank
    assert mrj.question("body") == rerank.question("body")
    assert (mrj.BODY_CHARS, mrj.GRAPH_SECTION, mrj.MODEL) == (
        rerank.BODY_CHARS, rerank.GRAPH_SECTION, rerank.DEFAULT_MODEL)
