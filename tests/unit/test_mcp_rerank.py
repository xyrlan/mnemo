"""The opt-in rerank stage of ``list_rules_by_topic`` (#401).

No test here opens a socket: the provider is a function the test hands in,
and the one test that lets ``apply`` build its own client proves it never
gets that far. What is pinned is the promise the README makes about the
stage — off unless configured, one request per list call, nothing sent
without a query, and every failure invisible to the agent but counted in the
access log.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core.config import DEFAULTS
from mnemo.core.mcp import rerank
from mnemo.core.mcp.server import handle_request
from mnemo.core.mcp.tools import RuleRefs

ON = {"recall": {"rerank": {"provider": "typesafe"}}}


@pytest.fixture(autouse=True)
def _no_project_resolution(monkeypatch):
    monkeypatch.setattr("mnemo.core.mcp.tools._resolve_current_project", lambda vault_root: None)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test reached the network")
    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.delenv(rerank.DEFAULT_KEY_ENV, raising=False)


def _write_page(vault: Path, slug: str, *, sources: int, body: str) -> None:
    target = vault / "shared" / "feedback"
    target.mkdir(parents=True, exist_ok=True)
    listed = "\n".join("  - bots/a/m%d.md" % i for i in range(sources))
    (target / (slug + ".md")).write_text(
        "---\nname: %s\ndescription: d\ntype: feedback\nstability: stable\n"
        "sources:\n%s\ntags:\n  - workflow\n---\n\n%s\n" % (slug, listed, body),
        encoding="utf-8")


@pytest.fixture
def vault(tmp_vault):
    _write_page(tmp_vault, "often-seen", sources=3, body="Squash before merge.")
    _write_page(tmp_vault, "the-one-that-matters", sources=2,
                body="Commit a script before running it on prod. " + rerank.GRAPH_SECTION + " [[links]]")
    _write_page(tmp_vault, "unrelated", sources=1, body="Name branches after the issue.")
    return tmp_vault


def _matches():
    return RuleRefs([{"slug": s, "type": "feedback", "source_count": n}
                     for s, n in (("often-seen", 3), ("the-one-that-matters", 2), ("unrelated", 1))])


class Judge:
    """Answers by rule text; records what it was sent."""

    def __init__(self, favourite="Commit a script", skip=()):
        self.calls = []
        self.favourite, self.skip = favourite, skip

    def __call__(self, state, questions):
        self.calls.append((state, questions))
        return {"answers": {
            key: {"noul": 0.9 if self.favourite in q["instructions"] else 0.1}
            for key, q in questions.items() if not any(s in q["instructions"] for s in self.skip)}}


def test_the_default_config_sends_nothing(vault):
    assert DEFAULTS["recall"]["rerank"]["provider"] == "none"
    judge = Judge()
    out, info = rerank.apply(vault, _matches(), "run a script on prod", project=None,
                             cfg=DEFAULTS, client=judge)
    assert info is None and not judge.calls
    assert [m["slug"] for m in out] == ["often-seen", "the-one-that-matters", "unrelated"]


def test_a_provider_nobody_wrote_is_off_not_on():
    assert rerank.settings({"recall": {"rerank": {"provider": "typesafe.ai"}}})["provider"] == "none"
    assert rerank.settings(None)["provider"] == "none"
    assert rerank.settings(ON)["model"] == "jev-1.13.0"


def test_the_pair_reading_judge_reorders_the_bucket_in_one_request(vault):
    judge = Judge()
    given = _matches()
    given.retired_withheld = 2
    out, info = rerank.apply(vault, given, "run a script on prod", project=None, cfg=ON, client=judge)
    assert [m["slug"] for m in out] == ["the-one-that-matters", "often-seen", "unrelated"]
    assert info == {"provider": "typesafe", "status": "ok", "judged": 3}
    assert len(judge.calls) == 1
    assert out.retired_withheld == 2


def test_what_is_sent_is_the_query_and_the_bounded_rule_text_and_nothing_else(vault):
    judge = Judge()
    rerank.apply(vault, _matches(), "run a script on prod", project=None, cfg=ON, client=judge)
    state, questions = judge.calls[0]
    assert state == {"developer_task": "run a script on prod"}
    sent = json.dumps(questions)
    assert "Commit a script before running it on prod." in sent
    assert "[[links]]" not in sent and "the-one-that-matters" not in sent and str(vault) not in sent
    assert len(rerank.rule_text("x" * 5000)) == rerank.BODY_CHARS


def test_without_a_query_nothing_is_sent(vault):
    judge = Judge()
    out, info = rerank.apply(vault, _matches(), None, project=None, cfg=ON, client=judge)
    assert info["status"] == "no_query" and not judge.calls


def test_without_a_key_the_order_stands_and_no_request_is_built(vault):
    out, info = rerank.apply(vault, _matches(), "q", project=None, cfg=ON)
    assert info["status"] == "no_key"
    assert [m["slug"] for m in out] == ["often-seen", "the-one-that-matters", "unrelated"]


@pytest.mark.parametrize("answer", [TimeoutError("slow"), {"answers": {}}, {"error": 429}, "not a dict"])
def test_a_provider_that_fails_leaves_the_order_that_came_in(vault, answer):
    def broken(state, questions):
        if isinstance(answer, Exception):
            raise answer
        return answer
    out, info = rerank.apply(vault, _matches(), "q", project=None, cfg=ON, client=broken)
    assert info["status"] == "error"
    assert [m["slug"] for m in out] == ["often-seen", "the-one-that-matters", "unrelated"]


def test_a_rule_the_judge_skipped_is_unjudged_not_irrelevant(vault):
    judge = Judge(favourite="Name branches", skip=("Squash",))
    out, info = rerank.apply(vault, _matches(), "q", project=None, cfg=ON, client=judge)
    assert [m["slug"] for m in out] == ["unrelated", "the-one-that-matters", "often-seen"]
    assert info["judged"] == 2


def test_a_bucket_over_the_cap_sends_its_head_and_keeps_its_tail(vault):
    judge = Judge()
    cfg = {"recall": {"rerank": {"provider": "typesafe", "maxRules": 2}}}
    out, _ = rerank.apply(vault, _matches(), "q", project=None, cfg=cfg, client=judge)
    assert len(judge.calls[0][1]) == 2
    assert [m["slug"] for m in out] == ["the-one-that-matters", "often-seen", "unrelated"]


def test_a_bool_is_not_a_probability():
    got = rerank.scores("q", {"a": "text"}, ["a"], lambda s, q: {"answers": {"r0": {"noul": True}}})
    assert got == {}


def _call(vault, cfg, arguments):
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "list_rules_by_topic", "arguments": arguments}}
    response = handle_request(request, vault_root=vault, cfg=cfg)
    return [r["slug"] for r in json.loads(response["result"]["content"][0]["text"])]


def _log(vault):
    path = vault / ".mnemo" / "mcp-access-log.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_the_server_reorders_only_when_configured_and_logs_what_happened(vault, monkeypatch):
    judge = Judge()
    real = rerank.apply
    monkeypatch.setattr(rerank, "apply", lambda *a, **k: real(*a, client=judge, **k))
    args = {"topic": "workflow", "query": "run a script on prod"}

    assert _call(vault, None, args)[0] == "often-seen"
    assert not judge.calls and "rerank" not in _log(vault)[-1]

    assert _call(vault, ON, args)[0] == "the-one-that-matters"
    assert _log(vault)[-1]["rerank"] == {"provider": "typesafe", "status": "ok", "judged": 3}
    assert _log(vault)[-1]["hit_slugs"][0] == "the-one-that-matters"


def test_a_configured_server_without_a_key_answers_as_before(vault):
    assert _call(vault, ON, {"topic": "workflow", "query": "q"})[0] == "often-seen"
    assert _log(vault)[-1]["rerank"]["status"] == "no_key"
