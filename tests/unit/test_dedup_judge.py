"""The judged dedupe queue: ``mnemo.core.dedup_judge`` and ``mnemo dedup-rules`` (#409).

Nothing here opens a socket. The judge is a function the test hands in, the
dry run is pinned to build no request at all, and the one path that would
construct a real client is proved never to reach it without a key. What is
pinned is what a queue for a curator has to be right about: every pair inside
a bucket asked about once however many topics hold it, the Jaccard columns
computed over the same text the judge reads, an unanswered pair counted apart
from "not a duplicate" and asked again next run, and — the promise the whole
command rests on — that nothing is ever merged without a maintainer naming
both slugs.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from mnemo.cli.parser import COMMANDS, _build_parser
from mnemo.core import dedup_judge as dj
from mnemo.core.extract.inbox.dedup import _bodies_similar
from mnemo.core.mcp import rerank

KEY = "sk-live-DO-NOT-PRINT-409"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test reached the network")
    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.delenv(rerank.DEFAULT_KEY_ENV, raising=False)


# --------------------------------------------------------------- the text


def test_the_judged_text_is_the_rule_without_its_link_section():
    body = "Commit the script before you run it.\n" + rerank.GRAPH_SECTION + "\n[[a]] [[b]]"
    assert dj.rule_text(body) == "Commit the script before you run it."
    assert dj.rule_text("a b c", max_chars=3) == "a b"


def test_jaccard_is_the_ratio_the_shipped_gate_thresholds():
    a, b = "one two three four", "one two three five"
    assert dj.jaccard(a, b) == pytest.approx(3 / 5)
    assert _bodies_similar(a, b, threshold=dj.JACCARD_GATE)
    assert dj.jaccard(a, "one two six seven") < dj.JACCARD_GATE
    assert not _bodies_similar(a, "one two six seven", threshold=dj.JACCARD_GATE)
    assert dj.jaccard("", "anything") == 0.0


def test_a_rule_with_no_body_is_in_no_pair():
    assert dj.pairs_of({"a": "x", "b": "", "c": "y"}) == [("a", "c")]


def test_the_estimate_counts_the_question_with_every_pair():
    bodies = {"a": "x" * 100, "b": "y" * 100}
    cost = dj.estimate(bodies)
    assert cost["pairs"] == 1
    assert cost["tokens"] == (200 + len(json.dumps(dj.QUESTION))) // 4
    assert cost["usd"] == round(cost["tokens"] * dj.USD_PER_MTOK / 1e6, 4)


# -------------------------------------------------------------- the pairs


def _bucket(project, topic, *slugs):
    return {"project": project, "topic": topic, "slugs": list(slugs)}


BODIES = {
    "measure-first": "gather real data before committing to a design decision",
    "baseline-before-tuning": "establish baseline metrics ahead of tuning any parameter",
    "lock-the-ledger": "hold the sweep lock while appending to the ledger file",
}


def test_a_pair_in_two_topics_is_asked_about_once():
    buckets = [_bucket("mnemo", "measurement", "measure-first", "baseline-before-tuning"),
               _bucket("mnemo", "workflow", "measure-first", "baseline-before-tuning",
                       "lock-the-ledger")]
    pairs, total = dj.plan_pairs(buckets, BODIES)
    assert total == 1 + 3
    assert len(pairs) == 3
    # Attributed to the first bucket in sorted order, so a re-run agrees.
    shared = next(r for r in pairs
                  if {r["a"], r["b"]} == {"measure-first", "baseline-before-tuning"})
    assert shared["topic"] == "measurement"
    assert shared["bucket_pairs"] == 1


def test_rank_is_by_jaccard_inside_the_bucket_and_covers_every_pair():
    buckets = [_bucket("mnemo", "workflow", *BODIES)]
    pairs, _total = dj.plan_pairs(buckets, BODIES)
    assert sorted(r["jaccard_rank"] for r in pairs) == [1, 2, 3]
    by_rank = sorted(pairs, key=lambda r: r["jaccard_rank"])
    assert [r["jaccard"] for r in by_rank] == sorted(
        (r["jaccard"] for r in pairs), reverse=True)


def test_the_largest_buckets_are_named_worst_first():
    biggest = dj.largest_buckets(
        [_bucket("p", "small", "a", "b"), _bucket("p", "big", "a", "b", "c", "d")], limit=1)
    assert biggest == [{"project": "p", "topic": "big", "rules": 4, "pairs": 6}]


# ------------------------------------------------------------ the request


def _judge(scores, calls=None):
    """A client answering from a ``{frozenset(pair bodies): score}`` table."""
    def client(state, questions):
        if calls is not None:
            calls.append((state, questions))
        score = scores.get(frozenset((state["rule_a"], state["rule_b"])))
        if score is None:
            return {"error": 429}
        return {"answers": {"same": {"type": "score", "score": score, "confidence": 0.5}},
                "usage": {"input_tokens": 100}}
    return client


def test_one_request_carries_both_bodies_and_the_question_and_nothing_else():
    calls = []
    dj.ask(_judge({}, calls), "body a", "body b")
    (state, questions), = calls
    assert state == {"rule_a": "body a", "rule_b": "body b"}
    assert questions == dj.QUESTION


def test_every_failure_is_an_unmeasured_pair_never_a_zero():
    def raising(state, questions):
        raise RuntimeError("provider down")

    for client in (raising, _judge({}), lambda s, q: {"answers": {"same": {"score": "two"}}}):
        assert dj.ask(client, "a", "b")["score"] is None


# -------------------------------------------------------------- the asking


def test_an_unanswered_pair_is_not_stored_so_a_re_run_asks_again(tmp_vault):
    pairs, _ = dj.plan_pairs([_bucket("mnemo", "workflow", *BODIES)], BODIES)
    scores = {frozenset((BODIES["measure-first"], BODIES["baseline-before-tuning"])): 1.8}
    fresh = dj.judge_pairs(pairs, BODIES, _judge(scores), model="m", workers=2)
    assert len(fresh) == 1
    dj.save_answers(tmp_vault, fresh)
    assert len(dj.missing(pairs, dj.load_answers(tmp_vault, model="m"))) == 2


def test_answers_are_flushed_as_they_arrive(tmp_vault):
    pairs, _ = dj.plan_pairs([_bucket("mnemo", "workflow", *BODIES)], BODIES)
    scores = {frozenset((BODIES[a], BODIES[b])): 1.0
              for a, b in (("measure-first", "baseline-before-tuning"),
                           ("measure-first", "lock-the-ledger"),
                           ("baseline-before-tuning", "lock-the-ledger"))}
    seen = []
    dj.judge_pairs(pairs, BODIES, _judge(scores), model="m", workers=1,
                   flush=lambda answers: seen.append(len(answers)), flush_every=1)
    assert seen == [1, 2, 3]


def test_an_answer_from_another_model_is_not_reused(tmp_vault):
    dj.save_answers(tmp_vault, {("a", "b"): {"a": "a", "b": "b", "model": "old", "score": 2.0}})
    assert dj.load_answers(tmp_vault, model="old")
    assert dj.load_answers(tmp_vault, model="new") == {}
    # A file nobody can parse costs the resume and nothing else.
    dj.answers_path(tmp_vault).write_text("{", encoding="utf-8")
    assert dj.load_answers(tmp_vault, model="old") == {}


# --------------------------------------------------------------- the queue


def test_the_queue_holds_what_cleared_the_bar_best_first():
    pairs, _ = dj.plan_pairs([_bucket("mnemo", "workflow", *BODIES)], BODIES)
    answers = {(r["a"], r["b"]): {"score": s, "confidence": 0.5, "input_tokens": 100}
               for r, s in zip(sorted(pairs, key=lambda r: (r["a"], r["b"])),
                               (1.9, 0.2, 1.6))}
    queue = dj.build_queue(pairs, answers, BODIES, {s: s.upper() for s in BODIES})
    assert [r["score"] for r in queue] == [1.9, 1.6]
    row = queue[0]
    assert row["name_a"] == row["a"].upper()
    assert row["opening_a"] == BODIES[row["a"]][:dj.OPENING_CHARS]
    assert row["jaccard_rank"] and row["over_gate"] is False
    summary = dj.summarize(pairs, answers, queue)
    assert summary["judged"] == 3 and summary["unmeasured"] == 0
    assert summary["queued"] == 2 and summary["queued_under_gate"] == 2


def test_a_tie_reads_the_pair_jaccard_ranks_lowest_first():
    pairs = [{"a": "a", "b": "b", "project": "p", "topic": "t", "jaccard": 0.5,
              "jaccard_rank": 1, "bucket_pairs": 2},
             {"a": "c", "b": "d", "project": "p", "topic": "t", "jaccard": 0.1,
              "jaccard_rank": 2, "bucket_pairs": 2}]
    answers = {("a", "b"): {"score": 2.0}, ("c", "d"): {"score": 2.0}}
    assert [r["a"] for r in dj.build_queue(pairs, answers, {}, {})] == ["c", "a"]


def test_an_unmeasured_pair_is_counted_apart_from_not_a_duplicate():
    pairs, _ = dj.plan_pairs([_bucket("mnemo", "workflow", *BODIES)], BODIES)
    answers = {(pairs[0]["a"], pairs[0]["b"]): {"score": 0.1, "input_tokens": 100}}
    summary = dj.summarize(pairs, answers, [])
    assert summary["pairs"] == 3 and summary["judged"] == 1 and summary["unmeasured"] == 2
    assert sum(summary["levels"].values()) == 1


# ----------------------------------------------------------------- vaults


def _page(vault: Path, slug: str, *, project: str, topics, body: str,
          page_type: str = "feedback") -> Path:
    folder = vault / "shared" / page_type
    folder.mkdir(parents=True, exist_ok=True)
    text = (
        "---\nname: %s\ndescription: d\ntype: %s\nstability: stable\n"
        "sources:\n  - bots/%s/memory/%s.md\ntags:\n%s\n---\n\n%s\n"
        % (slug, page_type, project, slug,
           "\n".join("  - %s" % t for t in topics), body)
    )
    path = folder / ("%s-file.md" % slug)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def vault(tmp_vault):
    """Two rules that say one thing in different words, and one that does not."""
    _page(tmp_vault, "measure-first", project="mnemo", topics=["measurement", "workflow"],
          body="Gather real data before committing to a design decision.")
    _page(tmp_vault, "baseline-before-tuning", project="mnemo",
          topics=["measurement", "workflow"],
          body="Establish baseline metrics ahead of tuning any parameter.",
          page_type="reference")
    _page(tmp_vault, "lock-the-ledger", project="mnemo", topics=["workflow"],
          body="Hold the sweep lock while appending to the ledger file.")
    from mnemo.core import rule_activation
    rule_activation.write_index(tmp_vault, rule_activation.build_index(tmp_vault))
    return tmp_vault


def _run(monkeypatch, vault: Path, argv: list):
    import mnemo.cli.commands  # noqa: F401 — populates COMMANDS

    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = _build_parser().parse_args(["dedup-rules", *argv])
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = COMMANDS["dedup-rules"](args)
    return code, out.getvalue(), err.getvalue()


def test_the_buckets_are_what_list_rules_by_topic_returns(vault):
    buckets = {(b["project"], b["topic"]): b["slugs"] for b in dj.buckets(vault)}
    assert set(buckets) == {("mnemo", "measurement"), ("mnemo", "workflow")}
    assert sorted(buckets[("mnemo", "measurement")]) == [
        "baseline-before-tuning", "measure-first"]
    assert len(buckets[("mnemo", "workflow")]) == 3
    # A topic with one rule in it is no bucket at all.
    assert dj.buckets(vault, topic="measurement", project="mnemo")[0]["slugs"]
    assert dj.buckets(vault, topic="nothing-here") == []


def test_bodies_are_read_once_per_slug_however_many_buckets_hold_it(vault):
    bodies, names = dj.load_bodies(vault, dj.buckets(vault))
    assert set(bodies) == {"measure-first", "baseline-before-tuning", "lock-the-ledger"}
    assert names["measure-first"] == "measure-first"
    assert bodies["measure-first"].startswith("Gather real data")
    assert rerank.GRAPH_SECTION not in bodies["measure-first"]


# ------------------------------------------------------------- the command


def test_the_dry_run_posts_nothing_and_prints_the_arithmetic(monkeypatch, vault):
    code, out, _err = _run(monkeypatch, vault, ["--judge"])
    assert code == 0
    assert "dry run — nothing has left this machine." in out
    # measurement holds 2 of the rules, workflow all 3 — and the pair both
    # topics hold is asked about once.
    assert "3 pairs to compare (4 bucket pairs; 1 of them" in out
    assert "input tokens" in out and "jev-1.13.0" in out
    assert rerank.TYPESAFE_URL in out
    assert not dj.queue_path(vault).exists()


def test_the_dry_run_json_is_the_same_numbers(monkeypatch, vault):
    code, out, _err = _run(monkeypatch, vault, ["--judge", "--json"])
    payload = json.loads(out)
    assert code == 0 and payload["pairs"] == 3 and payload["to_ask"] == 3
    assert payload["over_max_pairs"] is False
    assert payload["largest_buckets"][0]["topic"] == "workflow"


def test_over_max_pairs_refuses_and_names_the_largest_buckets(monkeypatch, vault):
    code, out, _err = _run(monkeypatch, vault, ["--judge", "--max-pairs", "1"])
    assert code == 0 and "refusing --send" in out and "largest buckets" in out
    code, _out, err = _run(monkeypatch, vault, ["--judge", "--max-pairs", "1", "--send"])
    assert code == 2 and "nothing was sent" in err and "workflow" in err


def test_send_without_a_key_says_where_to_get_one(monkeypatch, vault):
    code, _out, err = _run(monkeypatch, vault, ["--judge", "--send"])
    assert code == 2
    assert "mnemo rerank --setup" in err


def _stub_provider(monkeypatch, scores, seen=None):
    """Replace the module's client factory; the key never leaves this process."""
    def factory(key, *, model, timeout):
        assert key == KEY
        if seen is not None:
            seen.append(model)
        return _judge(scores)
    monkeypatch.setattr(rerank, "typesafe_client", factory)


def test_send_asks_writes_the_queue_and_resumes(monkeypatch, vault):
    monkeypatch.setenv(rerank.DEFAULT_KEY_ENV, KEY)
    bodies, _names = dj.load_bodies(vault, dj.buckets(vault))
    scores = {frozenset((bodies["measure-first"], bodies["baseline-before-tuning"])): 1.9}
    models = []
    _stub_provider(monkeypatch, scores, models)

    code, out, _err = _run(monkeypatch, vault, ["--judge", "--send"])
    assert code == 0
    assert models and models[0] == "jev-1.13.0"
    assert "measure-first" in out and "baseline-before-tuning" in out
    assert "nothing was merged" in out
    assert KEY not in out

    payload = json.loads(dj.queue_path(vault).read_text(encoding="utf-8"))
    assert payload["summary"]["queued"] == 1
    row, = payload["pairs"]
    assert {row["a"], row["b"]} == {"measure-first", "baseline-before-tuning"}
    assert row["jaccard"] < dj.JACCARD_GATE and row["over_gate"] is False
    assert row["opening_a"] and row["opening_b"]

    # Resumable: the pair that answered is not asked again, the two that
    # failed are — an error is not a score.
    assert len(dj.load_answers(vault, model="jev-1.13.0")) == 1
    _code, out, _err = _run(monkeypatch, vault, ["--judge"])
    assert "1 already answered by jev-1.13.0; 2 left to ask" in out


def test_a_run_with_nothing_left_to_ask_needs_no_key(monkeypatch, vault):
    """Re-reading the queue at another ``--at`` is not a reason to want a key."""
    bodies, _names = dj.load_bodies(vault, dj.buckets(vault))
    pairs, _total = dj.plan_pairs(dj.buckets(vault), bodies)
    dj.save_answers(vault, {(r["a"], r["b"]): dict(r, model="jev-1.13.0", score=2.0,
                                                  confidence=0.5, input_tokens=1)
                            for r in pairs})

    def never(*args, **kwargs):
        raise AssertionError("a run with nothing to ask built a client")
    monkeypatch.setattr(rerank, "typesafe_client", never)

    code, out, _err = _run(monkeypatch, vault, ["--judge", "--send", "--at", "1.9"])
    assert code == 0 and "nothing was sent" in out
    assert json.loads(dj.queue_path(vault).read_text(encoding="utf-8"))["summary"][
        "queued"] == len(pairs)


def test_send_does_not_need_the_recall_stage_to_be_on(monkeypatch, vault):
    """The queue is a maintenance command, not the recall path (#409)."""
    from mnemo.core import config as cfg_mod

    assert cfg_mod.load_config().get("recall", {}).get("rerank", {}).get(
        "provider", "none") == "none"
    assert dj.provider_settings(cfg_mod.load_config())["provider"] == "typesafe"
    monkeypatch.setenv(rerank.DEFAULT_KEY_ENV, KEY)
    _stub_provider(monkeypatch, {})
    code, _out, _err = _run(monkeypatch, vault, ["--judge", "--send"])
    assert code == 0


def test_the_judge_merges_nothing_by_itself(monkeypatch, vault):
    monkeypatch.setenv(rerank.DEFAULT_KEY_ENV, KEY)
    bodies, _names = dj.load_bodies(vault, dj.buckets(vault))
    _stub_provider(monkeypatch, {frozenset((bodies["measure-first"],
                                            bodies["baseline-before-tuning"])): 2.0})
    before = sorted(p.name for p in (vault / "shared").rglob("*.md"))
    _run(monkeypatch, vault, ["--judge", "--send"])
    assert sorted(p.name for p in (vault / "shared").rglob("*.md")) == before


# -------------------------------------------------------------- the merge


def test_merge_folds_the_sources_and_can_be_undone(monkeypatch, vault):
    from mnemo.core.reclassify_apply import undo

    code, out, err = _run(monkeypatch, vault,
                          ["--merge", "measure-first", "baseline-before-tuning"])
    assert code == 0, err
    keep = (vault / "shared" / "feedback" / "measure-first-file.md").read_text(encoding="utf-8")
    assert "bots/mnemo/memory/baseline-before-tuning.md" in keep
    assert not (vault / "shared" / "reference" / "baseline-before-tuning-file.md").exists()
    run_id = out.split("mnemo reclassify --undo ")[1].strip()

    # The dropped page's extraction-state entry is keyed by the type it had,
    # so the next extraction does not write it back.
    state = json.loads((vault / ".mnemo" / "extraction-state.json").read_text(encoding="utf-8"))
    assert state["entries"]["reference/baseline-before-tuning"]["status"] == "dismissed"

    assert undo(vault, run_id)
    assert (vault / "shared" / "reference" / "baseline-before-tuning-file.md").exists()
    assert (vault / "shared" / "feedback" / "measure-first-file.md").read_text(
        encoding="utf-8") == keep.replace(
            "  - bots/mnemo/memory/baseline-before-tuning.md\n", "")


def test_merge_refuses_a_slug_that_is_not_a_live_rule(monkeypatch, vault):
    for argv in (["--merge", "measure-first", "measure-first"],
                 ["--merge", "measure-first", "no-such-rule"],
                 ["--merge", "no-such-rule", "measure-first"]):
        code, _out, err = _run(monkeypatch, vault, argv)
        assert code == 2 and "error:" in err
    assert (vault / "shared" / "feedback" / "measure-first-file.md").exists()


def test_the_three_modes_are_mutually_exclusive():
    for argv in (["--judge", "--apply"], ["--judge", "--merge", "a", "b"]):
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["dedup-rules", *argv])


def test_the_name_keyed_dedupe_is_still_the_default(monkeypatch, vault):
    code, out, _err = _run(monkeypatch, vault, [])
    assert code == 0 and "no duplicates found" in out
