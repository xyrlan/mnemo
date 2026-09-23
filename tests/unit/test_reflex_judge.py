"""The opt-in judge that gates the reflex (#412).

No test here opens a socket: the provider is a function the test hands in,
and the tests that let :func:`judge.ask` build its own client prove it never
gets that far. What is pinned is the promise the docs make — off unless
configured, one request per prompt, the measured wording verbatim, a hard
timeout, and every failure invisible to the session but countable in the log.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from mnemo.core.config import DEFAULTS
from mnemo.core.mcp import rerank
from mnemo.core.reflex import judge
from mnemo.hooks import user_prompt_submit as hook

ON = {"reflex": {"judge": {"provider": "typesafe"}}}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test reached the network")
    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.delenv(judge.DEFAULT_KEY_ENV, raising=False)


class Judge:
    """Answers by rule text; records the state and the questions it was sent."""

    def __init__(self, scores=None, default=0.05):
        self.scores = scores or {}
        self.default = default
        self.calls = []

    def __call__(self, state, questions):
        self.calls.append((state, questions))
        answers = {}
        for key, block in questions.items():
            body = block["instructions"]
            value = self.default
            for needle, score in self.scores.items():
                if needle in body:
                    value = score
            answers[key] = {"noul": value}
        return {"answers": answers}


TEXTS = {"a": "Commit a script before running it on prod.",
         "b": "Name branches after the issue.",
         "c": "Squash before merge."}


def _read(slug):
    return TEXTS.get(slug, "")


# --- configuration -----------------------------------------------------------

def test_the_stage_is_off_in_the_shipped_defaults():
    chosen = judge.settings(DEFAULTS)
    assert chosen["provider"] == "none"
    assert chosen["model"] == "jev-1.13.0"
    assert chosen["candidates"] == 3
    assert chosen["injectAt"] == 0.4
    assert chosen["timeoutSeconds"] == 2.5
    assert chosen["keyEnv"] == rerank.DEFAULT_KEY_ENV


def test_the_default_bar_and_the_config_default_agree():
    """Two places state it; a change to one must be a change to both (#461)."""
    assert judge.DEFAULT_INJECT_AT == 0.4
    assert DEFAULTS["reflex"]["judge"]["injectAt"] == judge.DEFAULT_INJECT_AT


def test_a_config_that_sets_the_bar_keeps_it(tmp_path, monkeypatch):
    """#461 moved the default, not anyone's chosen value: a user file that
    says 0.6 is read as 0.6 through the real loader, merge included."""
    from mnemo.core import config as cfg_mod

    target = tmp_path / "mnemo.config.json"
    target.write_text(json.dumps({"reflex": {"judge": {"injectAt": 0.6}}}),
                      encoding="utf-8")
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(target))
    assert judge.settings(cfg_mod.load_config())["injectAt"] == 0.6

    target.write_text(json.dumps({"reflex": {"judge": {"provider": "typesafe"}}}),
                      encoding="utf-8")
    assert judge.settings(cfg_mod.load_config())["injectAt"] == 0.4


def test_an_unknown_provider_reads_as_off():
    """A typo must not start posting prompt text somewhere."""
    assert judge.settings({"reflex": {"judge": {"provider": "typsafe"}}})["provider"] == "none"


@pytest.mark.parametrize("block, key, expected", [
    ({"timeoutSeconds": "soon"}, "timeoutSeconds", judge.DEFAULT_TIMEOUT_S),
    ({"timeoutSeconds": None}, "timeoutSeconds", judge.DEFAULT_TIMEOUT_S),
    ({"injectAt": "high"}, "injectAt", judge.DEFAULT_INJECT_AT),
    ({"injectAt": True}, "injectAt", judge.DEFAULT_INJECT_AT),
    ({"candidates": 0}, "candidates", judge.DEFAULT_CANDIDATES),
    ({"candidates": "three"}, "candidates", judge.DEFAULT_CANDIDATES),
    ({"model": ""}, "model", judge.DEFAULT_MODEL),
])
def test_a_bad_value_falls_back_to_its_default(block, key, expected):
    assert judge.settings({"reflex": {"judge": block}})[key] == expected


def test_an_explicit_zero_bar_is_a_value_not_a_typo():
    """0 injects everything the judge scored, which is a thing to configure."""
    assert judge.settings({"reflex": {"judge": {"injectAt": 0}}})["injectAt"] == 0.0


@pytest.mark.parametrize("block", [
    None, {}, {"judge": {}}, {"judge": {"provider": "none"}},
    {"judge": {"provider": "typesafe"}}, {"judge": {"provider": "NONE"}},
    {"judge": {"provider": "typsafe"}}, {"judge": {"provider": ""}},
])
def test_the_hooks_cheap_peek_agrees_with_the_module(block):
    """The hook spells :func:`judge.enabled` out so the default never imports
    this module; the two answers must not drift."""
    assert hook._judge_configured(block) == judge.enabled(block)


# --- what is sent ------------------------------------------------------------

def test_the_question_is_the_measured_wording():
    """Pinned verbatim: every number in the report came from these sentences."""
    block = judge.question("RULE TEXT")
    assert block["type"] == "noul"
    assert block["instructions"] == (
        "This stored engineering rule should be shown to the AI coding assistant "
        "before it answers the developer's message in the state, because it bears "
        "directly on what that message asks for. Rule: RULE TEXT"
    )
    assert block["criteria"]["true"] == (
        "The rule is about the same problem, component or pitfall the message "
        "is about and would change what the assistant does")
    assert block["criteria"]["false"] == (
        "The rule is about something else, is too general to change anything, "
        "or the message is too short or context-dependent to tell what it is about")


def test_the_prompt_is_collapsed_then_cut():
    """Collapse first: a pasted diff should spend its budget on words."""
    text = "a\n\n   b\t c " + ("x" * 4000)
    sent = judge.state(text)["developer_message"]
    assert sent.startswith("a b c x")
    assert len(sent) == judge.PROMPT_CHARS
    assert "\n" not in sent


def test_one_request_carries_every_rule():
    client = Judge()
    judge.scores("how do I deploy", TEXTS, ["a", "b", "c"], client)
    assert len(client.calls) == 1
    state, questions = client.calls[0]
    assert state == {"developer_message": "how do I deploy"}
    assert len(questions) == 3


def test_a_rule_with_no_text_is_not_asked_about():
    client = Judge()
    found = judge.scores("q", {"a": TEXTS["a"], "b": ""}, ["a", "b"], client)
    assert set(found) == {"a"}
    assert len(client.calls[0][1]) == 1


def test_a_rule_the_judge_skipped_is_absent_not_zero():
    class Partial(Judge):
        def __call__(self, state, questions):
            return {"answers": {"r0": {"noul": 0.9}}}

    found = judge.scores("q", TEXTS, ["a", "b"], Partial())
    assert found == {"a": 0.9}


# --- choosing ----------------------------------------------------------------

def test_chosen_takes_the_bar_and_orders_best_first():
    assert judge.chosen({"a": 0.9, "b": 0.61, "c": 0.4}, 0.6) == ["a", "b"]


def test_chosen_breaks_ties_by_slug_so_a_report_and_the_hook_agree():
    assert judge.chosen({"b": 0.8, "a": 0.8}, 0.6) == ["a", "b"]


def test_the_bar_is_inclusive():
    assert judge.chosen({"a": 0.6}, 0.6) == ["a"]


# --- ask ---------------------------------------------------------------------

def _ask(tmp_path, slugs, client, **over):
    chosen = dict(judge.settings(ON), **over)
    return judge.ask(tmp_path, prompt="how do I deploy", slugs=slugs,
                     chosen_settings=chosen, client=client, read_text=_read)


def test_ask_returns_the_rules_over_the_bar(tmp_path):
    picks, info = _ask(tmp_path, ["a", "b"], Judge({"Commit a script": 0.8}))
    assert picks == ["a"]
    assert info["status"] == "ok"
    assert info["asked"] == 2 and info["injected"] == 1
    assert info["scores"][0] == ["a", 0.8]
    assert isinstance(info["ms"], int)


def test_nothing_over_the_bar_is_an_answer_not_a_failure(tmp_path):
    picks, info = _ask(tmp_path, ["a", "b"], Judge())
    assert picks == []
    assert info["status"] == "ok" and info["injected"] == 0


def test_an_empty_pool_asks_nothing(tmp_path):
    client = Judge()
    picks, info = _ask(tmp_path, [], client)
    assert picks == [] and info["asked"] == 0
    assert client.calls == []


def test_no_key_falls_back_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr("mnemo.core.secrets.read", lambda *a, **k: None)
    chosen = judge.settings(ON)
    picks, info = judge.ask(tmp_path, prompt="q", slugs=["a"],
                            chosen_settings=chosen, read_text=_read)
    assert picks is None
    assert info["status"] == "no_key"


def test_an_http_error_falls_back(tmp_path):
    def boom(state, questions):
        raise RuntimeError("500")

    picks, info = _ask(tmp_path, ["a"], boom)
    assert picks is None and info["status"] == "error"


def test_a_malformed_answer_falls_back(tmp_path):
    picks, info = _ask(tmp_path, ["a"], lambda state, questions: {"answers": {"r0": {}}})
    assert picks is None and info["status"] == "error"


def test_a_socket_timeout_is_reported_as_a_timeout(tmp_path):
    import socket

    def slow(state, questions):
        raise socket.timeout("timed out")

    picks, info = _ask(tmp_path, ["a"], slow)
    assert picks is None and info["status"] == "timeout"


def test_the_deadline_is_hard_even_when_the_client_never_returns(tmp_path):
    """``urllib``'s timeout bounds each socket read, not the request. A hook
    that runs before every prompt cannot rely on that."""
    started = time.time()
    # A real block, not `time.sleep` — conftest makes that a no-op. Released
    # at the end so the abandoned worker does not outlive the test.
    stuck = threading.Event()

    def never(state, questions):
        stuck.wait(30)
        raise AssertionError("the caller should have given up")

    try:
        picks, info = _ask(tmp_path, ["a"], never, timeoutSeconds=0.2)
        assert picks is None and info["status"] == "timeout"
        assert time.time() - started < 5
    finally:
        stuck.set()


def test_a_page_with_no_body_is_not_asked_about_and_costs_nothing(tmp_path):
    client = Judge()
    picks, info = judge.ask(tmp_path, prompt="q", slugs=["gone"],
                            chosen_settings=judge.settings(ON), client=client,
                            read_text=lambda slug: "")
    assert picks == [] and info["asked"] == 0
    assert client.calls == []


def test_the_log_row_never_carries_prompt_text(tmp_path):
    _picks, info = _ask(tmp_path, ["a", "b"], Judge({"Commit a script": 0.8}))
    blob = repr(info)
    assert "how do I deploy" not in blob
    assert set(info) == {"status", "asked", "injected", "ms", "scores"}


def test_the_key_is_the_one_the_list_stage_stores(monkeypatch, tmp_path):
    """One TypeSafe key per machine: same env var, same secrets entry."""
    monkeypatch.setenv(judge.DEFAULT_KEY_ENV, "from-the-env")
    key, source = judge.resolve_key(judge.settings(ON))
    assert (key, source) == ("from-the-env", "env")
