"""``tools/measure_jev_dedupe.py`` over built rule bodies and a stub judge (#187).

Nothing here touches the network: the judge is a function the test hands in,
which is also how the tool stays honest about what it sends — a test can read
every state the client was given. The arithmetic is what is pinned: the
Jaccard column must be the ratio the shipped gate thresholds, the rank must
be by that ratio, and a pair the judge could not answer must stay
"unmeasured" and never read as "not a duplicate".
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from mnemo.core.extract.inbox.dedup import _bodies_similar

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_jev_dedupe.py"
_spec = importlib.util.spec_from_file_location("measure_jev_dedupe", _TOOL)
mjd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mjd)

BODIES = {
    "measure-first": "gather real data before committing to a design decision",
    "baseline-before-tuning": "establish baseline metrics ahead of tuning any parameter",
    "lock-the-ledger": "hold the sweep lock while appending to the ledger file",
}


def _judge(scores, calls=None):
    """A client that answers from a ``{frozenset(pair bodies): score}`` table."""
    def client(state, questions):
        if calls is not None:
            calls.append((state, questions))
        score = scores.get(frozenset((state["rule_a"], state["rule_b"])))
        if score is None:
            return {"error": 429}
        return {"answers": {"same": {"type": "score", "score": score, "confidence": 0.5}},
                "usage": {"input_tokens": 100}}
    return client


def _key(a, b):
    return frozenset((BODIES[a], BODIES[b]))


def test_jaccard_is_the_ratio_the_shipped_gate_thresholds():
    a, b = "one two three four", "one two three five"
    assert mjd.jaccard(a, b) == pytest.approx(3 / 5)
    assert mjd.jaccard(a, b) >= mjd.JACCARD_GATE
    assert _bodies_similar(a, b, threshold=mjd.JACCARD_GATE)
    assert not _bodies_similar(a, "one two six seven", threshold=mjd.JACCARD_GATE)
    assert mjd.jaccard(a, "one two six seven") < mjd.JACCARD_GATE


def test_an_empty_body_is_no_pair():
    assert mjd.jaccard("", "anything") == 0.0
    assert mjd.pairs_of({"a": "x", "b": "", "c": "y"}) == [("a", "c")]


def test_every_pair_is_asked_once_with_both_bodies_and_nothing_else():
    calls = []
    mjd.judge(BODIES, _judge({}, calls), workers=1)
    assert len(calls) == 3
    for state, questions in calls:
        assert set(state) == {"rule_a", "rule_b"}
        assert state["rule_a"] in BODIES.values() and state["rule_b"] in BODIES.values()
        assert questions == mjd.QUESTION


def test_a_synonym_duplicate_is_found_where_jaccard_ranks_it_low():
    scores = {
        _key("measure-first", "baseline-before-tuning"): 1.8,
        _key("measure-first", "lock-the-ledger"): 0.1,
        _key("baseline-before-tuning", "lock-the-ledger"): 0.2,
    }
    rows = mjd.judge(BODIES, _judge(scores), workers=2)
    summary = mjd.summarize(rows)
    assert summary["judged_duplicate"] == 1
    assert summary["judged_duplicate_under_gate"] == 1
    assert summary["pairs_over_gate"] == 0
    assert summary["levels"] == {"0": 2, "1": 0, "2": 1}
    dupe = next(r for r in rows if r["score"] == 1.8)
    assert {dupe["a"], dupe["b"]} == {"measure-first", "baseline-before-tuning"}
    assert dupe["jaccard"] < mjd.JACCARD_GATE


def test_rank_is_by_jaccard_and_covers_every_pair():
    rows = mjd.judge(BODIES, _judge({}), workers=1)
    assert sorted(r["jaccard_rank"] for r in rows) == [1, 2, 3]
    by_rank = sorted(rows, key=lambda r: r["jaccard_rank"])
    assert [r["jaccard"] for r in by_rank] == sorted((r["jaccard"] for r in rows), reverse=True)


def test_an_unanswered_pair_is_unmeasured_not_a_non_duplicate():
    scores = {_key("measure-first", "baseline-before-tuning"): 1.8}
    rows = mjd.judge(BODIES, _judge(scores), workers=1)
    summary = mjd.summarize(rows)
    assert summary["pairs"] == 3
    assert summary["unmeasured"] == 2
    assert sum(summary["levels"].values()) == 1
    assert all(r["jaccard_rank"] for r in rows)


def test_cost_is_tokens_at_the_published_price():
    scores = {k: 0.0 for k in (
        _key("measure-first", "baseline-before-tuning"),
        _key("measure-first", "lock-the-ledger"),
        _key("baseline-before-tuning", "lock-the-ledger"),
    )}
    summary = mjd.summarize(mjd.judge(BODIES, _judge(scores), workers=1))
    assert summary["input_tokens"] == 300
    assert summary["usd"] == round(300 * mjd.USD_PER_MTOK / 1e6, 4)


def test_estimate_counts_pairs_without_calling_anything():
    cost = mjd.estimate(BODIES)
    assert cost["pairs"] == 3
    assert cost["tokens"] > 0


def test_without_send_nothing_is_posted(monkeypatch, capsys):
    monkeypatch.setattr(mjd, "load_bodies", lambda project, topic, max_chars: dict(BODIES))

    def boom(*args, **kwargs):
        raise AssertionError("a dry run opened a connection")

    monkeypatch.setattr(mjd.urllib.request, "urlopen", boom)
    assert mjd.main(["--project", "p", "--topic", "t"]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and "3 pairs" in out


def test_send_without_a_key_refuses(monkeypatch, capsys):
    monkeypatch.setattr(mjd, "load_bodies", lambda project, topic, max_chars: dict(BODIES))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert mjd.main(["--project", "p", "--topic", "t", "--send"]) == 1
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err


def test_the_model_is_pinned_not_an_alias():
    assert "latest" not in mjd.MODEL and "preview" not in mjd.MODEL


def test_listing_prints_the_top_pair_with_both_openings():
    scores = {_key("measure-first", "baseline-before-tuning"): 1.8}
    rows = mjd.judge(BODIES, _judge(scores), workers=1)
    text = mjd.format_report(mjd.summarize(rows), rows, BODIES, listing=1)
    assert "measure-first" in text and "baseline-before-tuning" in text
    assert BODIES["measure-first"] in text
