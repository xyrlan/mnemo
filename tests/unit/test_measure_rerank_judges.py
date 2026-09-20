"""``tools/measure_rerank_judges.py`` over built labels and a stub second judge (#401).

Nothing here starts a ``claude`` process: the judge is a function the test
hands in. What is pinned is what the headline number rests on — a judge's own
ordering must be flagged circular and never starred, an unanswered rule must
stay unjudged and be asked again rather than read as 0, an interrupted run
must resume without paying twice, and a run without ``--send`` must call
nothing.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_rerank_judges.py"
_spec = importlib.util.spec_from_file_location("measure_rerank_judges", _TOOL)
mrk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mrk)


def test_the_prompt_numbers_the_rules_from_one_under_the_task():
    prompt = mrk.build_prompt("fix the ledger race", ["lock it", "measure first"])
    assert prompt.startswith("TASK: fix the ledger race")
    assert "1. lock it" in prompt and "2. measure first" in prompt


def test_only_a_valid_0_1_2_becomes_a_label():
    answer = {"1": 2, "2": "1", "3": 3, "4": "yes", "5": None, "6": True, "7": 0}
    chunk = ["a", "b", "c", "d", "e", "f", "g", "h"]  # h has no answer at all
    assert mrk.parse_labels(answer, chunk) == {"a": 2, "b": 1, "g": 0}


def test_pending_chunks_skip_done_and_empty_rules_and_are_stable():
    bodies = {"r%02d" % i: "body" for i in range(mrk.CHUNK + 3)}
    bodies["empty"] = ""
    chunks = mrk.pending_chunks(bodies, {"r00": 2})
    assert [len(c) for c in chunks] == [mrk.CHUNK, 2]
    assert "r00" not in chunks[0] and all("empty" not in c for c in chunks)
    assert chunks == mrk.pending_chunks(dict(reversed(list(bodies.items()))), {"r00": 2})


def test_judging_fills_labels_and_saves_after_every_call():
    bodies = {"r%02d" % i: "body %d" % i for i in range(mrk.CHUNK + 1)}
    done, saves, prompts = {}, [], []

    def caller(prompt, system):
        prompts.append(prompt)
        assert system == mrk.SYSTEM
        return {str(i + 1): 1 for i in range(prompt.count("\n") - 2)}

    failed = mrk.judge_unit("task", bodies, done, caller, on_chunk=lambda: saves.append(len(done)))
    assert failed == 0
    assert len(done) == mrk.CHUNK + 1 and set(done.values()) == {1}
    assert saves == [mrk.CHUNK, mrk.CHUNK + 1]
    assert len(prompts) == 2


def test_a_failed_call_loses_nothing_and_the_next_run_asks_only_for_the_gap():
    bodies = {"r%02d" % i: "body" for i in range(mrk.CHUNK * 2)}
    done = {}
    calls = {"n": 0}

    def flaky(prompt, system):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("rate limited")
        return {str(i + 1): 2 for i in range(mrk.CHUNK)}

    assert mrk.judge_unit("task", bodies, done, flaky) == 1
    assert len(done) == mrk.CHUNK  # the first chunk is kept
    asked = []
    mrk.judge_unit("task", bodies, done, lambda p, s: asked.append(p) or {str(i + 1): 0 for i in range(mrk.CHUNK)})
    assert len(asked) == 1  # only the chunk that failed
    assert len(done) == mrk.CHUNK * 2


def test_a_partly_answered_chunk_leaves_the_rest_unjudged_not_zero():
    done = {}
    mrk.judge_unit("task", {"a": "x", "b": "y"}, done, lambda p, s: {"1": 2})
    assert done == {"a": 2}
    assert mrk.pending_chunks({"a": "x", "b": "y"}, done) == [["b"]]


def test_rerank_is_by_signal_ties_keep_order_and_unscored_rules_sink():
    order = ["a", "b", "c", "d", "e"]
    assert mrk.rerank(order, {"a": 0.1, "b": 0.9, "c": 0.9, "e": 0.0}) == ["b", "c", "a", "e", "d"]
    assert mrk.rerank(order, {}) == order


def test_bootstrap_interval_brackets_the_mean_and_is_reproducible():
    deltas = [0.1, 0.2, 0.0, 0.3, 0.1, 0.2, 0.15, 0.05]
    low, high = mrk.bootstrap_ci(deltas)
    assert 0 < low < sum(deltas) / len(deltas) < high
    assert (low, high) == mrk.bootstrap_ci(deltas)
    assert mrk.bootstrap_ci([]) == (0.0, 0.0)
    zero_low, zero_high = mrk.bootstrap_ci([0.2, -0.2, 0.1, -0.1])
    assert zero_low < 0 < zero_high


def test_auc_needs_both_classes():
    assert mrk.auc([0.9, 0.8], [0.1, 0.8]) == pytest.approx((1 + 1 + 1 + 0.5) / 4)
    assert mrk.auc([], [0.1]) is None


def _fixture():
    """Two queries where BM25F buries the rule both judges want first."""
    slugs = ["s%d" % i for i in range(8)]
    units, second, shipped = [], [], []
    for want in ("s6", "s7"):
        noul = {s: 0.05 for s in slugs}
        noul[want] = 0.95
        labels = {s: 0 for s in slugs}
        labels[want] = 2
        units.append({"noul": noul, "reads": ["s0"]})
        second.append(labels)
        shipped.append(list(slugs))
    return units, second, shipped


def test_the_crossed_rows_carry_the_result_and_a_self_graded_row_is_circular():
    units, second, shipped = _fixture()
    rows = {(r["grader"], r["ordering"]): r for r in mrk.compare(units, second, shipped)}
    crossed = rows[("second", "rerank by first")]
    assert not crossed["circular"]
    assert crossed["delta"] > 0 and crossed["ndcg"] == pytest.approx(1.0)
    assert (crossed["should_read_top"], crossed["should_read"]) == (2, 2)
    assert rows[("second", "shipped")]["should_read_top"] == 0
    assert rows[("second", "rerank by second")]["circular"]
    assert rows[("first", "rerank by first")]["circular"]
    assert not rows[("first", "rerank by second")]["circular"]
    assert rows[("first", "shipped")]["delta"] == 0.0


def test_the_read_label_is_reported_beside_the_judged_ones():
    units, second, shipped = _fixture()
    rows = {(r["grader"], r["ordering"]): r for r in mrk.compare(units, second, shipped)}
    assert (rows[("second", "shipped")]["read_top"], rows[("second", "shipped")]["read"]) == (2, 2)
    # s0 was read; the judges' order pushes it to second place, still in the top 5
    assert rows[("second", "rerank by first")]["read_top"] == 2


def test_a_circular_row_is_never_starred():
    units, second, shipped = _fixture()
    text = mrk.format_rows(mrk.compare(units, second, shipped))
    for line in text.splitlines():
        if "circular" in line:
            assert " * " not in line and not line.rstrip().endswith("*")


def test_a_query_the_grader_finds_nothing_relevant_in_is_left_out():
    units, second, shipped = _fixture()
    second[1] = {s: 0 for s in second[1]}
    rows = [r for r in mrk.compare(units, second, shipped) if r["grader"] == "second"]
    assert {r["queries"] for r in rows} == {1}
    assert {r["queries"] for r in mrk.compare(units, second, shipped) if r["grader"] == "first"} == {2}


def test_agreement_counts_only_pairs_both_judges_scored():
    units, second, _ = _fixture()
    del second[0]["s1"]
    result = mrk.agreement([u["noul"] for u in units], second)
    assert result["pairs"] == 15
    assert result["second_counts"] == {"0": 13, "1": 0, "2": 2}
    assert result["first_should_read"] == 2
    assert result["auc_should_read"] == pytest.approx(1.0)


def _vault(tmp_path, monkeypatch):
    units, second, shipped = _fixture()
    for i, u in enumerate(units):
        u.update({"project": "p", "topic": "t", "query": "q%d" % i})
    (tmp_path / ".mnemo").mkdir()
    (tmp_path / ".mnemo" / mrk.mrj.QRELS_NAME).write_text(json.dumps({
        "model": "jev-1.13.0", "question_version": 1, "judged_at": "x", "units": units}), encoding="utf-8")
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: tmp_path)
    monkeypatch.setattr(mrk.mrj, "_bodies", lambda vault, unit: {s: "body " + s for s in unit["noul"]})
    monkeypatch.setattr(mrk.mrj, "_orders", lambda vault, units, gate, shipped=False: [
        sorted(u["noul"]) for u in units])
    return units, second


def test_judge_without_send_calls_nothing(tmp_path, monkeypatch, capsys):
    _vault(tmp_path, monkeypatch)
    monkeypatch.setattr(mrk, "_cli_caller", lambda p, s: pytest.fail("a dry run called the judge"))
    assert mrk.main(["--judge"]) == 0
    assert "dry run: 2 calls" in capsys.readouterr().out
    assert not (tmp_path / ".mnemo" / mrk.SECOND_NAME).exists()


def test_send_writes_labels_then_a_second_send_has_nothing_left_to_ask(tmp_path, monkeypatch, capsys):
    _vault(tmp_path, monkeypatch)
    calls = []

    def caller(prompt, system):
        calls.append(prompt)
        return {str(i + 1): (2 if "body s6" in line or "body s7" in line else 0)
                for i, line in enumerate(prompt.split("RULES:\n")[1].splitlines())}

    monkeypatch.setattr(mrk, "_cli_caller", caller)
    assert mrk.main(["--judge", "--send"]) == 0
    saved = json.loads((tmp_path / ".mnemo" / mrk.SECOND_NAME).read_text(encoding="utf-8"))
    assert saved["model"] == mrk.SECOND_MODEL and saved["prompt_version"] == mrk.PROMPT_VERSION
    assert sum(len(v) for v in saved["labels"].values()) == 16
    assert len(calls) == 2
    assert mrk.main(["--judge", "--send"]) == 0
    assert len(calls) == 2  # resumed: nothing pending, nothing paid for twice
    capsys.readouterr()
    assert mrk.main([]) == 0
    out = capsys.readouterr().out
    assert "graded by the second judge" in out and "circular" in out


def test_labels_made_by_an_older_prompt_are_not_reused(tmp_path, monkeypatch, capsys):
    _vault(tmp_path, monkeypatch)
    (tmp_path / ".mnemo" / mrk.SECOND_NAME).write_text(json.dumps({
        "model": mrk.SECOND_MODEL, "prompt_version": mrk.PROMPT_VERSION - 1,
        "labels": {"stale": {"s0": 2}}}), encoding="utf-8")
    assert mrk.main([]) == 1
    assert "no second-judge labels" in capsys.readouterr().err


def test_comparing_without_first_judge_labels_says_where_to_get_them(tmp_path, monkeypatch):
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: tmp_path)
    with pytest.raises(SystemExit) as exc:
        mrk.main([])
    assert "measure_recall_judged.py" in str(exc.value)
