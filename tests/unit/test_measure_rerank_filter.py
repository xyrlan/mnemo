"""``tools/measure_rerank_filter.py`` over synthetic transcripts and synthetic labels.

Nothing here opens a socket: the judge is a function the test hands in, and
the dry run is pinned to build no request at all. What is pinned is what a
measurement tool has to be right about — the id a label is filed under, the
pairing that turns a transcript into a unit, the arithmetic of the filter
table, and the fact that the signal it grades is the one
``mnemo.core.mcp.rerank`` ships. A tool that scores a signal the stage does
not compute measures nothing, so
``test_the_tool_and_the_stage_agree_on_the_signal_and_the_order`` runs both
over the same numbers and compares.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from mnemo.core.mcp import rerank
from mnemo.core.mcp.tools import RuleRefs

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_rerank_filter.py"
_spec = importlib.util.spec_from_file_location("measure_rerank_filter", _TOOL)
mrf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mrf)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test reached the network")
    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.delenv(rerank.DEFAULT_KEY_ENV, raising=False)


# --- the transcript side -----------------------------------------------------


def _use(name, uid, **args):
    return {"cwd": "/w/repo", "timestamp": "2026-09-20T10:00:00.000Z", "message": {"content": [
        {"type": "tool_use", "id": uid, "name": name, "input": args}]}}


def _result(uid, slugs):
    text = json.dumps([{"slug": s, "type": "feedback", "source_count": 1} for s in slugs])
    return {"message": {"content": [
        {"type": "tool_result", "tool_use_id": uid, "content": [{"type": "text", "text": text}]}]}}


def test_the_id_is_the_identity_of_one_list_call():
    args = ("/w/repo", "workflow", "fix the ledger race", "2026-09-20T10:00:00.000Z")
    assert mrf.unit_id(*args) == mrf.unit_id(*args)
    assert len(mrf.unit_id(*args)) == 10
    moved = mrf.unit_id("/w/other", *args[1:])
    assert moved != mrf.unit_id(*args)
    # The split is a function of the id, so it cannot be re-drawn per run.
    assert mrf.part_of("0" * 10) == "test" and mrf.part_of("1" * 10) == "dev"
    assert {mrf.part_of(mrf.unit_id(*args))} <= {"dev", "test"}


def test_a_call_is_paired_with_its_result_and_a_read_with_the_list_that_showed_it():
    records = [
        _use("mcp__mnemo__list_rules_by_topic", "t1", topic="workflow", query="first task"),
        _result("t1", ["a", "b"]),
        _use("mcp__mnemo__read_mnemo_rule", "r1", slug="b"),
        _use("mcp__mnemo__list_rules_by_topic", "t2", topic="git", query="second task"),
        _result("t2", ["b", "c"]),
        _use("mcp__mnemo__read_mnemo_rule", "r2", slug="b"),
        _use("mcp__mnemo__read_mnemo_rule", "r3", slug="never-shown"),
    ]
    calls = mrf.calls_from_records(records)
    assert [(c["topic"], c["shown"], c["reads"]) for c in calls] == [
        ("workflow", ["a", "b"], ["b"]),
        ("git", ["b", "c"], ["b"]),
    ]


def test_a_call_without_a_query_or_without_a_result_is_not_a_unit():
    records = [
        _use("mcp__mnemo__list_rules_by_topic", "t1", topic="workflow"),
        _result("t1", ["a", "b"]),
        _use("mcp__mnemo__list_rules_by_topic", "t2", topic="git", query="q"),
        _use("mcp__mnemo__list_rules_by_topic", "t3", topic="api", query="q"),
        _result("t3", ["a", "b"]),
    ]
    assert [c["topic"] for c in mrf.calls_from_records(records)] == ["api"]


@pytest.mark.parametrize("content,expected", [
    ([{"type": "text", "text": '[{"slug": "a"}, {"slug": "b"}]'}], ["a", "b"]),
    ('[{"slug": "a"}]', ["a"]),
    ([{"type": "text", "text": "Error: topic not found"}], []),
    ([{"type": "text", "text": '{"slug": "a"}'}], []),
    (None, []),
])
def test_only_a_real_list_result_yields_slugs(content, expected):
    assert mrf.result_slugs(content) == expected


def test_a_rule_with_no_text_is_dropped_and_a_short_list_with_it():
    call = {"cwd": "/w/repo", "topic": "workflow", "query": "q", "ts": "t",
            "shown": ["a", "gone", "b", "a"], "reads": ["gone", "b"]}
    unit = mrf.unit_from_call(call, {"a": "text a", "gone": "", "b": "text b"})
    assert unit["shown"] == ["a", "b"] and unit["reads"] == ["b"]
    assert unit["texts"] == {"a": "text a", "b": "text b"}
    assert unit["repo"] == "repo"
    assert mrf.unit_from_call(dict(call, shown=["a", "gone"]), {"a": "t", "gone": ""}) is None


def test_a_pre_cutover_list_of_names_is_resolved_to_slugs():
    """Lists logged before 2026-09-08 hold rule names, which match no page."""
    call = {"cwd": "/w/repo", "topic": "workflow", "query": "q", "ts": "t",
            "shown": ["Squash before merge", "b"], "reads": ["Squash before merge"]}
    unit = mrf.unit_from_call(call, {"squash-before-merge": "text", "b": "text b"},
                              {"Squash before merge": "squash-before-merge"})
    assert unit["shown"] == ["squash-before-merge", "b"]
    assert unit["reads"] == ["squash-before-merge"]


# --- the report --------------------------------------------------------------


def _unit(uid, slugs, *, query="q", reads=()):
    return {"uid": uid, "repo": "repo", "topic": "workflow", "query": query, "ts": "t",
            "shown": list(slugs), "reads": list(reads),
            "texts": {s: "body of " + s for s in slugs}}


def _signals(units, noul, bm25f=None):
    return {u["uid"]: mrf.unit_signals(u, {s: [v] for s, v in noul[u["uid"]].items()},
                                       (bm25f or {}).get(u["uid"], {}),
                                       weight=rerank.DEFAULT_BM25_WEIGHT)
            for u in units}


def test_the_filter_table_counts_what_the_agent_would_be_offered():
    units = [_unit("a1", ["hit", "junk1", "junk2"]), _unit("a2", ["junk3", "junk4"])]
    labels = {"a1": {"hit": 2, "junk1": 0, "junk2": 1}, "a2": {"junk3": 0, "junk4": 0}}
    noul = {"a1": {"hit": 0.9, "junk1": 0.1, "junk2": 0.5}, "a2": {"junk3": 0.1, "junk4": 0.2}}
    signals = _signals(units, noul)

    whole = mrf.filter_row("whole list", None, units, labels, signals)
    assert whole["rules_per_query"] == 2.5 and whole["should_read_kept"] == 1
    assert whole["junk_share"] == 0.6 and whole["empty"] == 0

    kept = mrf.filter_row("jev", 0.4, units, labels, signals)
    assert kept["rules_per_query"] == 1.0
    assert kept["should_read_kept"] == 1 and kept["should_read"] == 1
    assert kept["junk_share"] == 0.0
    # The second query keeps nothing, and it had no should-read rule to lose:
    # an empty answer is only a failure when it drops one.
    assert kept["empty"] == 1 and kept["empty_with_should_read"] == 0

    strict = mrf.filter_row("jev", 0.95, units, labels, signals)
    assert strict["should_read_kept"] == 0
    assert strict["empty"] == 2 and strict["empty_with_should_read"] == 1


def test_a_rule_the_judge_never_scored_is_not_kept_by_the_judge_filter():
    units = [_unit("a1", ["scored", "unscored"])]
    labels = {"a1": {"scored": 0, "unscored": 2}}
    signals = _signals(units, {"a1": {"scored": 0.9}})
    row = mrf.filter_row("jev", 0.5, units, labels, signals)
    assert row["rules_per_query"] == 1.0 and row["should_read_kept"] == 0


def test_the_rerank_rows_pair_every_ordering_against_the_order_that_was_shown():
    units = [_unit("a1", ["junk", "hit", "related"], reads=["hit"])]
    labels = {"a1": {"junk": 0, "hit": 2, "related": 1}}
    orders = {"shown": [["junk", "hit", "related"]],
              "jev": [["hit", "related", "junk"]]}
    rows = {r["ordering"]: r for r in mrf.rerank_rows(units, labels, orders, k=2)}
    assert rows["shown"]["delta"] == 0.0 and rows["shown"]["ci"] == [0.0, 0.0]
    assert rows["jev"]["ndcg"] == 1.0 and rows["jev"]["delta"] > 0
    assert rows["jev"]["pairwise"] == 1.0 and rows["shown"]["pairwise"] < 1.0
    assert rows["shown"]["junk_top"] == 1 and rows["jev"]["junk_top"] == 0
    assert rows["shown"]["read_top"] == 1 and rows["jev"]["read"] == 1
    assert rows["jev"]["should_read_top"] == 1 and rows["jev"]["should_read"] == 1


def test_the_ceiling_row_is_printed_as_circular_and_never_starred():
    """An oracle graded by its own labels scores 1.000 and means nothing."""
    units = [_unit("a1", ["junk", "hit"])]
    labels = {"a1": {"junk": 0, "hit": 2}}
    orders = {"shown": [["junk", "hit"]], "oracle": [["hit", "junk"]]}
    report = {
        "units": 1, "pairs": 2, "rater": "r", "blind": True,
        "label_counts": {"0": 1, "1": 0, "2": 1}, "scores_key": mrf.SCORES_KEY,
        "scored": 2, "part": "test", "queries": 1, "dev": 0, "test": 1,
        "retest": {"pairs": 0, "exact": 0, "confusion": {}},
        "filter": [mrf.filter_row("whole list", None, units, labels,
                                  _signals(units, {"a1": {"hit": 0.9, "junk": 0.1}}))],
        "rerank": mrf.rerank_rows(units, labels, orders, k=1),
        "average_precision": {"shown": 0.5},
    }
    printed = mrf.format_report(report)
    assert "circular: the ceiling" in printed
    assert "*" not in printed.split("oracle")[1].split("circular")[0]


def test_pairwise_accuracy_is_within_one_query_and_none_without_a_pair():
    assert mrf.pairwise(["a", "b"], {"a": 2, "b": 0}) == 1.0
    assert mrf.pairwise(["a", "b"], {"a": 0, "b": 2}) == 0.0
    assert mrf.pairwise(["a", "b", "c"], {"a": 2, "b": 0, "c": 2}) == 0.5
    assert mrf.pairwise(["a", "b"], {"a": 1, "b": 1}) is None
    assert mrf.pairwise(["a", "b"], {}) is None


def test_average_precision_is_pooled_and_ties_keep_the_incoming_order():
    assert mrf.average_precision([(0.9, True), (0.5, False), (0.1, True)]) == 0.8333
    assert mrf.average_precision([(0.1, False), (0.9, True)]) == 1.0
    assert mrf.average_precision([(0.5, False)]) == 0.0
    assert mrf.average_precision([(0.5, False), (0.5, True)]) == 0.5


def test_the_retest_joins_the_two_label_files_on_what_the_rater_saw():
    """The sample file keys pairs by task and rule text, not by uid."""
    units = [_unit("a1", ["one", "two"])]
    sample = [
        {"task": "q", "rule": "body of one", "label": 2},
        {"task": "q", "rule": "body of two", "label": 0},
        {"task": "q", "rule": "a rule this population never showed", "label": 1},
        {"task": "q", "rule": "body of one", "label": None},
    ]
    got = mrf.retest(sample, units, {"a1": {"one": 2, "two": 1}})
    assert got["pairs"] == 2 and got["exact"] == 1
    assert got["confusion"]["2->2"] == 1 and got["confusion"]["0->1"] == 1
    assert mrf.retest([], units, {}) == {"pairs": 0, "exact": 0,
                                         "confusion": dict.fromkeys(got["confusion"], 0)}


# --- the blind round ---------------------------------------------------------


def test_a_blind_chunk_shows_the_task_and_the_rule_and_nothing_else():
    units = [_unit("a1", ["one", "two"], query="first task"),
             _unit("a2", ["three"], query="second task")]
    # The rule text is the only thing a rater may see, so nothing in this
    # fixture's text repeats a slug: the assertion below has to be able to
    # fail.
    for unit in units:
        unit["texts"] = {slug: "a rule about %d things" % i
                         for i, slug in enumerate(unit["texts"])}
    chunks, ids = mrf.blind_chunks(units, {}, limit=2)
    # A unit is never split, so the second unit starts a second file.
    assert [[len(entry["rules"]) for entry in chunk] for chunk in chunks] == [[2], [1]]
    assert chunks[0][0]["task"] == "first task"
    assert sorted(chunks[0][0]["rules"][0]) == ["id", "rule"]
    sent = json.dumps(chunks)
    assert "a1" not in sent and "one" not in sent and "shown" not in sent
    assert ids == {"0": ["a1", "one"], "1": ["a1", "two"], "2": ["a2", "three"]}


def test_a_unit_larger_than_the_limit_is_its_own_chunk_rather_than_split():
    chunks, ids = mrf.blind_chunks([_unit("a1", ["a", "b", "c"])], {}, limit=2)
    assert len(chunks) == 1 and len(chunks[0][0]["rules"]) == 3


def test_a_pair_already_labelled_is_not_asked_again():
    units = [_unit("a1", ["one", "two"])]
    chunks, ids = mrf.blind_chunks(units, {"a1": {"one": 0}})
    assert ids == {"0": ["a1", "two"]}
    assert mrf.blind_chunks(units, {"a1": {"one": 0, "two": 2}}) == ([], {})


def test_importing_labels_is_everything_or_nothing():
    ids = {"0": ["a1", "one"], "1": ["a1", "two"]}
    folded, problems = mrf.imported_labels(ids, {"0": 2, "1": 0})
    assert folded == {"a1": {"one": 2, "two": 0}} and problems == []

    _, missing = mrf.imported_labels(ids, {"0": 2})
    assert missing == ["no label for id 1"]

    _, bad = mrf.imported_labels(ids, {"0": 3, "1": True})
    assert bad == ["id 0: 3 is not 0, 1 or 2", "id 1: True is not 0, 1 or 2"]

    _, unknown = mrf.imported_labels(ids, {"0": 2, "1": 2, "9": 1})
    assert unknown == ["id 9 is not in the export"]


# --- asking the judge --------------------------------------------------------


def test_a_pair_already_scored_is_never_asked_again():
    unit = _unit("a1", ["a", "b", "c"])
    assert mrf.pending_chunks(unit, {}) == [["a", "b", "c"]]
    assert mrf.pending_chunks(unit, {"b": [0.4]}) == [["a", "c"]]
    assert mrf.pending_chunks(unit, {"a": [0.1], "b": [0.4], "c": [0.9]}) == []
    assert mrf.pending_chunks(unit, {}, size=2) == [["a", "b"], ["c"]]
    # A rule with no text is not a pair: the judge reads the text.
    assert mrf.pending_chunks(dict(unit, texts={"a": "", "b": "x"}), {}) == [["b"]]


def test_the_dry_run_prints_the_cost_and_builds_no_request(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    units = [_unit("a1", ["a", "b"])]
    (vault / ".mnemo" / mrf.UNITS_NAME).write_text(
        json.dumps({"units": units}), encoding="utf-8")
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)

    assert mrf.main(["--score"]) == 0
    printed = capsys.readouterr().out
    assert "dry run: 2 pairs" in printed and rerank.TYPESAFE_URL in printed
    assert not (vault / ".mnemo" / mrf.SCORES_NAME).exists()


def test_send_without_a_key_asks_nobody(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    (vault / ".mnemo" / mrf.UNITS_NAME).write_text(
        json.dumps({"units": [_unit("a1", ["a", "b"])]}), encoding="utf-8")
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    assert mrf.main(["--score", "--send"]) == 1
    assert rerank.DEFAULT_KEY_ENV in capsys.readouterr().err


def test_mining_refuses_to_orphan_the_labels_on_disk(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    path = vault / ".mnemo" / mrf.UNITS_NAME
    path.write_text(json.dumps({"units": [_unit("a1", ["a", "b"])]}), encoding="utf-8")
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    assert mrf.main(["--mine", "--projects", str(tmp_path / "none")]) == 1
    assert "every label is keyed to its uids" in capsys.readouterr().err
    assert json.loads(path.read_text(encoding="utf-8"))["units"][0]["uid"] == "a1"


def test_the_blind_round_trips_through_a_directory(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    units = [_unit("a1", ["one", "two"])]
    (vault / ".mnemo" / mrf.UNITS_NAME).write_text(json.dumps({"units": units}), encoding="utf-8")
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    out = tmp_path / "blind"

    assert mrf.main(["--export-blind", str(out)]) == 0
    chunk = json.loads((out / "chunk-001.json").read_text(encoding="utf-8"))
    ids = json.loads((out / "ids.json").read_text(encoding="utf-8"))["ids"]
    answers = {str(rule["id"]): 2 for entry in chunk for rule in entry["rules"]}
    (out / "chunk-001.labels.json").write_text(json.dumps(answers), encoding="utf-8")

    assert mrf.main(["--import-labels", str(out), "--rater", "someone"]) == 0
    written = json.loads((vault / ".mnemo" / "recall-labels-full-someone.json")
                         .read_text(encoding="utf-8"))
    assert written["rater"] == "someone" and written["blind"] is True
    assert written["labels"] == {"a1": {ids[k][1]: 2 for k in ids}}


def test_a_partial_label_set_is_refused_and_writes_nothing(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    (vault / ".mnemo" / mrf.UNITS_NAME).write_text(
        json.dumps({"units": [_unit("a1", ["one", "two"])]}), encoding="utf-8")
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    out = tmp_path / "blind"
    assert mrf.main(["--export-blind", str(out)]) == 0
    (out / "chunk-001.labels.json").write_text(json.dumps({"0": 2}), encoding="utf-8")
    assert mrf.main(["--import-labels", str(out), "--rater", "someone"]) == 1
    assert "nothing written" in capsys.readouterr().err
    assert not (vault / ".mnemo" / "recall-labels-full-someone.json").exists()


# --- the tool and the stage --------------------------------------------------


def test_the_tool_and_the_stage_agree_on_the_signal_and_the_order(tmp_vault, monkeypatch):
    """The rules the tool's fused row keeps are the ones the stage marks.

    Two implementations of one threshold would be two measurements. The stage
    is run here with a stub judge and the tool over the same numbers, and the
    order and the kept set have to match exactly.
    """
    monkeypatch.setattr("mnemo.core.mcp.tools._resolve_current_project", lambda vault_root: None)
    bodies = {"alpha": "Commit a script before running it on prod.",
              "beta": "Squash before merge.",
              "gamma": "Name branches after the issue."}
    target = tmp_vault / "shared" / "feedback"
    target.mkdir(parents=True)
    for slug, body in bodies.items():
        (target / (slug + ".md")).write_text(
            "---\nname: %s\ndescription: d\ntype: feedback\nstability: stable\n"
            "sources:\n  - bots/a/m.md\ntags:\n  - workflow\n---\n\n%s\n" % (slug, body),
            encoding="utf-8")
    judged = {"alpha": 0.9, "beta": 0.6, "gamma": 0.1}
    local = {"alpha": 1.0, "beta": 4.0}
    monkeypatch.setattr(rerank, "bm25f_scores", lambda root, query, slugs: dict(local))

    def judge(state, questions):
        by_text = {rerank.rule_text(body): value for body, value in
                   ((bodies[slug], judged[slug]) for slug in bodies)}
        return {"answers": {key: {"noul": next(
            v for text, v in by_text.items() if text in q["instructions"])}
            for key, q in questions.items()}}

    matches = RuleRefs([{"slug": s, "type": "feedback", "source_count": 1}
                        for s in ("gamma", "beta", "alpha")])
    out, info = rerank.apply(tmp_vault, matches, "run a script on prod", project=None,
                             cfg={"recall": {"rerank": {"provider": "typesafe"}}}, client=judge)
    staged_order = [m["slug"] for m in out]
    staged_relevant = [m["slug"] for m in out if m.get("relevant")]

    unit = _unit("a1", ["gamma", "beta", "alpha"], query="run a script on prod")
    signals = _signals([unit], {"a1": judged}, {"a1": local})
    fused = signals["a1"]["fused"]
    assert rerank.ranked(unit["shown"], fused) == staged_order
    assert mrf.kept_slugs(unit, fused, rerank.DEFAULT_RELEVANT_AT) == staged_relevant
    assert info["relevant"] == len(staged_relevant)

    # And the tool's table counts that same kept set.
    row = mrf.filter_row("fused", rerank.DEFAULT_RELEVANT_AT, [unit],
                         {"a1": {"alpha": 2, "beta": 1, "gamma": 0}}, signals)
    assert row["rules_per_query"] == float(len(staged_relevant))


def test_the_estimators_are_the_siblings_and_not_a_second_copy():
    assert mrf.mrj.ndcg is mrf.mrj.ndcg and mrf.CHUNK == mrf.mrj.CHUNK
    assert mrf.thresholds("") == (mrf.BM25F_AT, mrf.JEV_AT, rerank.DEFAULT_RELEVANT_AT)
    assert mrf.thresholds("1,2,3") == (1.0, 2.0, 3.0)
    with pytest.raises(SystemExit):
        mrf.thresholds("1,2")
