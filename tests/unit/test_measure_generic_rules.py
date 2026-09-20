"""``tools/measure_generic_rules.py`` over synthetic rules and synthetic labels.

Nothing here opens a socket: the judge is a function the test hands in, the
``_no_network`` fixture makes ``urlopen`` an assertion failure, and the dry run
is pinned to build no request at all.

What a measurement tool has to be right about, and what is pinned below: the id
a label is filed under and the split that follows from it; that the stratified
draw is deterministic, balanced and shuffled; that the blind view carries
nothing but the id, the name and the rule; that an import is all-or-nothing;
that the two signals point the same way after :func:`genericness`; and the
arithmetic of the AUC rows, the threshold table and the rater agreement.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from mnemo.core.mcp import rerank

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_generic_rules.py"
_spec = importlib.util.spec_from_file_location("measure_generic_rules", _TOOL)
mgr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mgr)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test reached the network")
    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.delenv(rerank.DEFAULT_KEY_ENV, raising=False)


def _candidate(slug, *, project="alpha", name=None, rule=None):
    return {"slug": slug, "name": name or ("rule " + slug), "type": "feedback",
            "projects": [project] if project else [],
            "rule": rule if rule is not None else ("body of " + slug)}


# --- the sample --------------------------------------------------------------


def test_the_id_is_the_slug_and_the_split_follows_from_it():
    assert mgr.rule_id("a-slug") == mgr.rule_id("a-slug")
    assert len(mgr.rule_id("a-slug")) == 10
    assert mgr.rule_id("a-slug") != mgr.rule_id("b-slug")
    # Parity of the md5, so the halves cannot be re-drawn around a threshold.
    assert mgr.part_of("0" * 10) == "dev" and mgr.part_of("f" * 10) == "test"
    assert {mgr.part_of(mgr.rule_id(s)) for s in ("a", "b", "c", "d")} <= {"dev", "test"}


def test_the_draw_is_stratified_deterministic_and_shuffled():
    candidates = ([_candidate("big-%02d" % i, project="big") for i in range(40)]
                  + [_candidate("small-%d" % i, project="small") for i in range(2)])
    drawn = mgr.draw(candidates, per_project=6, seed=1)
    projects = [r["project"] for r in drawn]
    # One big project cannot be the whole sample, and a project with fewer
    # rules than asked gives what it has.
    assert projects.count("big") == 6 and projects.count("small") == 2
    assert drawn == mgr.draw(candidates, per_project=6, seed=1)
    assert [r["slug"] for r in drawn] != [r["slug"] for r in mgr.draw(
        candidates, per_project=6, seed=2)]
    # Not in project blocks: a rater must not read the stratification off the
    # order of the cards.
    assert projects != sorted(projects)


def test_a_rule_with_no_text_is_not_drawn_and_a_projectless_rule_is_its_own_stratum():
    candidates = [_candidate("has-text"), _candidate("no-text", rule=""),
                  _candidate("universal", project=None)]
    drawn = mgr.draw(candidates, per_project=6, seed=1)
    assert sorted(r["slug"] for r in drawn) == ["has-text", "universal"]
    assert mgr.stratum({"projects": []}) == "-"
    assert mgr.stratum({"projects": ["zeta", "alpha"]}) == "alpha"


def test_the_blind_view_carries_the_id_the_name_and_the_rule_and_nothing_else():
    entry = mgr.draw([_candidate("s", project="secret-project")], seed=1)[0]
    seen = mgr.blind(entry)
    assert set(seen) == {"id", "name", "rule"} == set(mgr.VISIBLE)
    # No project, no slug, no path, no score can reach a rater.
    assert "secret-project" not in json.dumps(seen)
    assert "slug" not in seen and "project" not in seen


def test_blind_chunks_skip_what_the_rater_already_answered():
    entries = mgr.draw([_candidate("s%02d" % i) for i in range(10)],
                       per_project=10, seed=1)
    chunks = mgr.blind_chunks(entries, {}, limit=4)
    assert [len(c) for c in chunks] == [4, 4, 2]
    done = {entries[0]["id"]: 0, entries[1]["id"]: 2}
    left = [item["id"] for chunk in mgr.blind_chunks(entries, done, limit=4) for item in chunk]
    assert len(left) == 8 and entries[0]["id"] not in left


# --- importing labels --------------------------------------------------------


def test_an_import_is_all_or_nothing():
    known = ["aa", "bb", "cc"]
    labels, problems = mgr.imported_labels(["aa", "bb"], {"aa": 0, "bb": 2}, known)
    assert labels == {"aa": 0, "bb": 2} and problems == []

    _, missing = mgr.imported_labels(["aa", "bb"], {"aa": 0}, known)
    assert missing == ["no label for bb"]

    _, off_scale = mgr.imported_labels(["aa"], {"aa": 3}, known)
    assert off_scale == ["aa: 3 is not 0, 1 or 2"]

    # A bool is not a label: ``True == 1`` would import silently.
    _, boolean = mgr.imported_labels(["aa"], {"aa": True}, known)
    assert boolean == ["aa: True is not 0, 1 or 2"]

    _, stranger = mgr.imported_labels([], {"zz": 1}, known)
    assert stranger == ["zz is not a rule in the sample"]


# --- the signals -------------------------------------------------------------


def test_both_wordings_point_the_same_way_after_genericness():
    # The loss question scores what a rule is worth, so its bottom level is the
    # generic end; the artifact question is a probability of naming something.
    assert mgr.genericness("loss", 0.0) == 1.0
    assert mgr.genericness("loss", 2.0) == 0.0
    assert mgr.genericness("loss", 1.0) == 0.5
    assert mgr.genericness("artifact", 0.0) == 1.0
    assert mgr.genericness("artifact", 1.0) == 0.0
    # An answer off the scale is clamped, never allowed past 1 or under 0.
    assert mgr.genericness("loss", 9.0) == 0.0 and mgr.genericness("loss", -9.0) == 1.0


def test_the_baseline_counts_what_a_rule_names():
    generic = "Measure before optimizing, and verify rather than assume."
    specific = ("`rerank.fuse` in `src/mnemo/core/mcp/rerank.py` normalises by the "
                "best bm25f score in tools/measure_rerank_filter.py")
    assert mgr.named_things(generic) == []
    named = mgr.named_things(specific)
    assert "rerank.fuse" in named and "src/mnemo/core/mcp/rerank.py" in named
    assert "tools/measure_rerank_filter.py" in named
    # Deduplicated, and the baseline is a genericness: names nothing is 1.0.
    assert mgr.named_things("`a` and `a` again") == ["a"]
    assert mgr.baseline_signal(generic) == 1.0
    assert mgr.baseline_signal(specific) < mgr.baseline_signal(generic)


def test_the_judge_and_the_rater_read_the_same_thing():
    view = mgr.rule_view("Always run the suite", "PYTHONPATH=src  python3 -m pytest\n")
    assert view == "Always run the suite. PYTHONPATH=src python3 -m pytest"
    for variant in mgr.VARIANTS:
        assert view in json.dumps(mgr.question(variant, view))
    # The rule text is the stage's, bounded and stripped of the link section.
    assert rerank.rule_text("body " + rerank.GRAPH_SECTION + " links") == "body"


def test_signals_leave_out_a_rule_the_judge_never_answered_for():
    entries = mgr.draw([_candidate("a"), _candidate("b")], per_project=5, seed=1)
    first, second = entries[0]["id"], entries[1]["id"]
    values = mgr.signals(entries, {"loss": {first: 2.0, second: None}})
    assert values["loss"] == {first: 0.0}
    # The local baseline always has every rule: it needs no request.
    assert set(values[mgr.BASELINE]) == {first, second}


# --- asking the judge --------------------------------------------------------


def test_the_dry_run_builds_no_request_and_resumes_where_it_stopped():
    entries = mgr.draw([_candidate("s%02d" % i) for i in range(5)], per_project=5, seed=1)
    batches = mgr.pending(entries, {}, size=2)
    assert [len(b) for b in batches] == [2, 2, 1]
    cost = mgr.estimate("loss", batches)
    assert cost["rules"] == 5 and cost["requests"] == 3 and cost["tokens"] > 0
    done = {e["id"]: 1.0 for e in entries[:3]}
    assert sum(len(b) for b in mgr.pending(entries, done, size=2)) == 2


def test_one_request_carries_the_chunk_and_an_unanswered_rule_is_absent():
    entries = mgr.draw([_candidate("a"), _candidate("b")], per_project=5, seed=1)
    seen = {}

    def client(state, questions):
        seen["state"], seen["questions"] = state, questions
        return {"answers": {"r0": {"score": 1.5}, "r1": {"score": None}}}

    answered = mgr.ask("loss", entries, client)
    assert answered == {entries[0]["id"]: 1.5}
    assert sorted(seen["questions"]) == ["r0", "r1"]
    assert seen["questions"]["r0"]["type"] == "score"
    assert len(seen["questions"]["r0"]["criteria"]) == 3
    assert mgr.ask("loss", [], client) == {}


def test_the_artifact_variant_reads_its_own_answer_field():
    entries = mgr.draw([_candidate("a")], per_project=5, seed=1)

    def client(state, questions):
        assert questions["r0"]["type"] == "noul"
        return {"answers": {"r0": {"noul": 0.25}}}

    assert mgr.ask("artifact", entries, client) == {entries[0]["id"]: 0.25}


# --- the report --------------------------------------------------------------


def _graded(pairs):
    """Rules whose baseline text is fixed, with a label and a judge answer."""
    entries = mgr.draw([_candidate("s%02d" % i) for i in range(len(pairs))],
                       per_project=len(pairs), seed=1)
    labels, scores = {}, {}
    for entry, (label, raw) in zip(entries, pairs):
        labels[entry["id"]] = label
        scores[entry["id"]] = raw
    return entries, labels, {"loss": scores}


def test_auc_separates_each_class_from_the_rest():
    # Three generic rules the judge scores 0 (nothing lost), three specific
    # ones it scores 2: perfect separation both ways.
    entries, labels, scores = _graded([(0, 0.0), (0, 0.0), (0, 0.0),
                                       (2, 2.0), (2, 2.0), (2, 2.0)])
    values = mgr.signals(entries, scores)["loss"]
    row = mgr.auc_row(values, labels, [e["id"] for e in entries])
    assert row["rules"] == 6
    assert row["auc_generic"] == 1.0 and row["auc_project_specific"] == 1.0
    # A signal that is the same everywhere is a coin flip, not a detector.
    flat = {e["id"]: 0.5 for e in entries}
    assert mgr.auc_row(flat, labels, [e["id"] for e in entries])["auc_generic"] == 0.5
    # No positives means no answer, never a zero.
    only_one = {e["id"]: 1 for e in entries}
    assert mgr.auc_row(values, only_one, [e["id"] for e in entries])["auc_generic"] is None


def test_the_threshold_row_counts_the_catch_and_the_expensive_error():
    entries, labels, scores = _graded([(0, 0.0), (0, 1.0), (1, 1.0), (2, 0.0), (2, 2.0)])
    values = mgr.signals(entries, scores)["loss"]
    ids = [e["id"] for e in entries]
    # genericness: 1.0, 0.5, 0.5, 1.0, 0.0 — a bar at 0.75 flags the two 1.0s,
    # one of which is a project-specific rule the judge got wrong.
    row = mgr.threshold_row(values, labels, ids, 0.75)
    assert row == {"threshold": 0.75, "rules": 5, "flagged": 2, "generic_flagged": 1,
                   "generic": 2, "project_flagged": 1, "project": 2}
    assert mgr.threshold_row(values, labels, ids, 1.5)["flagged"] == 0


def test_two_raters_agreement_is_the_error_bar():
    first = {"a": 0, "b": 1, "c": 2, "d": 0}
    second = {"a": 0, "b": 2, "c": 0, "e": 1}
    result = mgr.agreement(first, second)
    assert result["rules"] == 3 and result["exact"] == 1
    # b: 1 -> 2 is off by one; c: 2 -> 0 is the disagreement that matters.
    assert result["off_by_two"] == 1
    assert result["confusion"]["2->0"] == 1 and result["confusion"]["1->2"] == 1


def test_the_report_splits_by_part_and_names_the_raters_it_compared():
    entries, labels, scores = _graded([(0, 0.0), (0, 0.0), (2, 2.0), (2, 2.0),
                                       (1, 1.0), (1, 1.0)])
    other = dict(labels)
    result = mgr.report(entries, {"human": labels, "second": other}, scores,
                        rater="human", thresholds=(0.5, 0.75))
    assert result["rules"] == 6
    assert result["dev"] + result["test"] == 6
    assert result["label_counts"] == {"0": 2, "1": 2, "2": 2}
    assert sorted(result["auc"]) == sorted(list(mgr.VARIANTS) + [mgr.BASELINE])
    assert result["auc"]["loss"]["all"]["auc_generic"] == 1.0
    assert [r["threshold"] for r in result["thresholds"]["dev"]] == [0.5, 0.75]
    assert result["agreement_between"] == ["human", "second"]
    assert result["agreement"]["exact"] == result["agreement"]["rules"] == 6
    # Every part of the printed form comes from the dict; none of it recomputes.
    text = mgr.format_report(result)
    assert mgr.BASELINE in text and "off by two" in text
    assert "no number above is tighter than this." in text


def test_one_rater_leaves_the_agreement_block_out():
    entries, labels, scores = _graded([(0, 0.0), (2, 2.0)])
    result = mgr.report(entries, {"human": labels}, scores, rater="human")
    assert result["agreement"] is None and result["agreement_between"] == []
    assert "off by two" not in mgr.format_report(result)


# --- the vault side ----------------------------------------------------------


def _write_rule(vault: Path, slug: str, *, project: str, body: str) -> None:
    target = vault / "shared" / "feedback"
    target.mkdir(parents=True, exist_ok=True)
    (target / (slug + ".md")).write_text(
        "---\nname: %s\ndescription: d\ntype: feedback\nstability: stable\n"
        "sources:\n  - bots/%s/briefings/sessions/x.md\ntags:\n  - workflow\n---\n\n%s\n"
        % (slug, project, body), encoding="utf-8")


@pytest.fixture
def vault(tmp_vault, monkeypatch):
    for i in range(4):
        _write_rule(tmp_vault, "alpha-%d" % i, project="alpha", body="body %d" % i)
    for i in range(4):
        _write_rule(tmp_vault, "beta-%d" % i, project="beta", body="body %d" % i)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: tmp_vault, raising=False)
    return tmp_vault


def test_sample_export_import_and_report_round_trip(vault, tmp_path, capsys):
    assert mgr.main(["--sample", "--per-project", "2"]) == 0
    sample = json.loads((vault / ".mnemo" / mgr.SAMPLE_NAME).read_text(encoding="utf-8"))
    assert len(sample["rules"]) == 4
    assert {r["project"] for r in sample["rules"]} == {"alpha", "beta"}
    assert sample["question_version"] == mgr.QUESTION_VERSION

    out = tmp_path / "blind"
    assert mgr.main(["--export-blind", str(out)]) == 0
    chunks = sorted(p for p in out.glob("chunk-*.json"))
    exported = [item for path in chunks
                for item in json.loads(path.read_text(encoding="utf-8"))]
    assert len(exported) == 4 and all(set(i) == set(mgr.VISIBLE) for i in exported)

    # A missing answer is refused whole, and writes nothing.
    (out / "chunk-001.labels.json").write_text(
        json.dumps({exported[0]["id"]: 0}), encoding="utf-8")
    assert mgr.main(["--import-labels", str(out), "--rater", "ana"]) == 1
    assert not (vault / ".mnemo" / mgr.labels_name("ana")).exists()

    (out / "chunk-001.labels.json").write_text(
        json.dumps({item["id"]: i % 3 for i, item in enumerate(exported)}), encoding="utf-8")
    assert mgr.main(["--import-labels", str(out), "--rater", "ana"]) == 0
    written = json.loads(
        (vault / ".mnemo" / mgr.labels_name("ana")).read_text(encoding="utf-8"))
    assert written["rater"] == "ana" and written["blind"] is True
    assert len(written["labels"]) == 4

    # Redrawing now would throw those labels away.
    capsys.readouterr()
    assert mgr.main(["--sample"]) == 1
    assert "already holds labels" in capsys.readouterr().err

    # The report runs with no scores at all: the local baseline is enough.
    assert mgr.main(["--rater", "ana"]) == 0
    printed = capsys.readouterr().out
    assert mgr.BASELINE in printed and "4 rules" in printed
    assert mgr.main(["--rater", "ana", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["rater"] == "ana"


def test_the_dry_run_sends_nothing_and_needs_no_key(vault, capsys):
    assert mgr.main(["--sample", "--per-project", "2"]) == 0
    capsys.readouterr()
    assert mgr.main(["--score"]) == 0
    printed = capsys.readouterr().out
    assert "dry run" in printed and rerank.TYPESAFE_URL in printed
    assert not (vault / ".mnemo" / mgr.SCORES_NAME).exists()


def _stub_client(answers, calls, fail_first=False):
    state = {"asked": 0}

    def build(key, *, model, timeout):
        def ask(_state, questions):
            state["asked"] += 1
            calls.append(sorted(questions))
            if fail_first and state["asked"] == 1:
                raise OSError("provider down")
            return {"answers": {k: dict(answers) for k in questions}}
        return ask
    return build


def test_send_writes_resumable_scores_the_report_then_reads(vault, capsys, monkeypatch):
    monkeypatch.setattr(rerank, "resolve_key", lambda chosen: ("a-key", "secrets"))
    calls = []
    monkeypatch.setattr(rerank, "typesafe_client", _stub_client({"score": 0.0}, calls))
    assert mgr.main(["--sample", "--per-project", "2"]) == 0
    capsys.readouterr()

    assert mgr.main(["--score", "--send"]) == 0
    assert "key from secrets" in capsys.readouterr().out
    saved = json.loads((vault / ".mnemo" / mgr.SCORES_NAME).read_text(encoding="utf-8"))
    # Metadata under its own key, never beside the variants.
    assert set(saved) == {"loss", "_meta"} and len(saved["loss"]) == 4
    assert saved["_meta"]["question_version"] == mgr.QUESTION_VERSION

    # A second run asks for nothing: every rule already has an answer.
    calls.clear()
    assert mgr.main(["--score", "--send"]) == 0
    assert calls == []

    # The other wording is a separate key, not an overwrite.
    monkeypatch.setattr(rerank, "typesafe_client", _stub_client({"noul": 1.0}, calls))
    assert mgr.main(["--score", "--send", "--variant", "artifact"]) == 0
    saved = json.loads((vault / ".mnemo" / mgr.SCORES_NAME).read_text(encoding="utf-8"))
    assert len(saved["loss"]) == len(saved["artifact"]) == 4

    sample = json.loads((vault / ".mnemo" / mgr.SAMPLE_NAME).read_text(encoding="utf-8"))
    (vault / ".mnemo" / mgr.labels_name("ana")).write_text(json.dumps(
        {"rater": "ana", "labels": {r["id"]: i % 3
                                    for i, r in enumerate(sample["rules"])}}), encoding="utf-8")
    capsys.readouterr()
    assert mgr.main(["--rater", "ana", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["scored"] == {"artifact": 4, "loss": 4, mgr.BASELINE: 4}
    # score 0.0 is "nothing would be lost" and noul 1.0 is "names something":
    # after genericness the two point opposite ways on the same rules.
    assert set(mgr.signals(sample["rules"], saved)["loss"].values()) == {1.0}
    assert set(mgr.signals(sample["rules"], saved)["artifact"].values()) == {0.0}


def test_a_failed_request_keeps_what_was_already_paid_for(vault, capsys, monkeypatch):
    monkeypatch.setattr(rerank, "resolve_key", lambda chosen: ("a-key", "env"))
    monkeypatch.setattr(mgr, "CHUNK", 2)
    calls = []
    monkeypatch.setattr(rerank, "typesafe_client",
                        _stub_client({"score": 2.0}, calls, fail_first=True))
    assert mgr.main(["--sample", "--per-project", "2"]) == 0
    capsys.readouterr()
    # Four rules, two per request, and the first request dies: the exit code
    # says so and the second request's answers are on disk anyway.
    assert mgr.main(["--score", "--send"]) == 1
    assert "1 failed request(s)" in capsys.readouterr().out
    saved = json.loads((vault / ".mnemo" / mgr.SCORES_NAME).read_text(encoding="utf-8"))
    assert len(saved["loss"]) == 2

    # The re-run asks only for the two that are missing, and pays once for them.
    calls.clear()
    monkeypatch.setattr(rerank, "typesafe_client", _stub_client({"score": 2.0}, calls))
    assert mgr.main(["--score", "--send"]) == 0
    assert len(calls) == 1 and len(calls[0]) == 2
    saved = json.loads((vault / ".mnemo" / mgr.SCORES_NAME).read_text(encoding="utf-8"))
    assert len(saved["loss"]) == 4


def test_send_without_a_key_stops_before_the_request(vault, capsys, monkeypatch):
    monkeypatch.setattr(rerank, "resolve_key", lambda chosen: (None, "none"))
    assert mgr.main(["--sample", "--per-project", "2"]) == 0
    capsys.readouterr()
    assert mgr.main(["--score", "--send"]) == 1
    assert "found no key" in capsys.readouterr().err
    assert not (vault / ".mnemo" / mgr.SCORES_NAME).exists()


def test_a_report_without_a_sample_or_without_labels_says_so(vault, capsys):
    with pytest.raises(SystemExit):
        mgr.main([])
    assert mgr.main(["--sample", "--per-project", "2"]) == 0
    capsys.readouterr()
    assert mgr.main([]) == 1
    assert "no labels" in capsys.readouterr().err
