"""#441: subscription vs API billing when the CLI no longer sends apiKeySource."""
from __future__ import annotations

import json
import subprocess

import pytest

from mnemo.cli.commands import extract as extract_cmd
from mnemo.core import extract as extract_mod
from mnemo.core import llm
from tests.conftest import MockCompletedProcess

# The shape Claude Code 2.1.280 prints: one result event, no init event, and no
# apiKeySource anywhere (captured 2026-09-22, trimmed to the keys llm reads).
_ENVELOPE_2_1_280 = json.dumps([{
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "ok",
    "total_cost_usd": 0.021,
    "usage": {"input_tokens": 10, "output_tokens": 2},
}])


def _status(**fields) -> str:
    base = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}
    base.update(fields)
    return json.dumps(base)


class _Asked(list):
    answer: dict


@pytest.fixture
def auth_status(monkeypatch):
    """Stub ``claude auth status``; returns the list of argv it was asked with."""
    asked = _Asked()
    answer: dict = {"stdout": _status(), "raise": None}

    def fake(argv, **_kw):
        asked.append(argv)
        if answer["raise"] is not None:
            raise answer["raise"]
        return MockCompletedProcess(stdout=answer["stdout"])

    monkeypatch.setattr(llm, "_auth_status_run", fake)
    asked.answer = answer
    return asked


def _call(mock_subprocess_run, stdout=_ENVELOPE_2_1_280):
    mock_subprocess_run([MockCompletedProcess(stdout=stdout)])
    return llm.call("reply ok", system=None, model="claude-haiku-4-5", timeout=60)


def test_claude_ai_login_without_field_is_subscription(mock_subprocess_run, auth_status):
    resp = _call(mock_subprocess_run)
    assert resp.api_key_source == "none"  # normalised: old readers keep working
    assert llm.billing(resp) == "subscription"
    assert auth_status[0][1:] == ["auth", "status", "--json"]


def test_other_auth_method_without_field_is_api(mock_subprocess_run, auth_status):
    auth_status.answer["stdout"] = _status(authMethod="api_key")
    resp = _call(mock_subprocess_run)
    assert resp.api_key_source == "api_key"
    assert llm.billing(resp) == "api"


@pytest.mark.parametrize("answer", [
    {"raise": FileNotFoundError("claude")},
    {"raise": subprocess.TimeoutExpired(cmd=["claude"], timeout=15)},
    {"stdout": "not json"},
    {"stdout": _status(loggedIn=False)},
    {"stdout": _status(authMethod=None)},
])
def test_unreadable_login_is_unknown(mock_subprocess_run, auth_status, answer):
    auth_status.answer.update(answer)
    resp = _call(mock_subprocess_run)
    assert resp.api_key_source is None
    assert llm.billing(resp) == "unknown"


def test_api_key_in_env_is_api_without_asking(mock_subprocess_run, auth_status, monkeypatch):
    # `claude auth status` still says claude.ai with ANTHROPIC_API_KEY set,
    # while `claude --print` authenticates with the key.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    resp = _call(mock_subprocess_run)
    assert llm.billing(resp) == "api"
    assert auth_status == []


def test_field_from_the_cli_wins_and_login_is_not_asked(mock_subprocess_run, auth_status):
    auth_status.answer["stdout"] = _status(authMethod="api_key")
    envelope = json.dumps([
        {"type": "system", "subtype": "init", "apiKeySource": "none"},
        {"type": "result", "result": "ok", "total_cost_usd": 0.01},
    ])
    resp = _call(mock_subprocess_run, envelope)
    assert llm.billing(resp) == "subscription"
    assert auth_status == []

    resp = _call(mock_subprocess_run, json.dumps(
        {"type": "result", "result": "ok", "apiKeySource": "ANTHROPIC_API_KEY"}))
    assert llm.billing(resp) == "api"
    assert auth_status == []


def test_login_is_asked_once_per_process(mock_subprocess_run, auth_status):
    for _ in range(3):
        assert llm.billing(_call(mock_subprocess_run)) == "subscription"
    assert len(auth_status) == 1


def test_failed_login_lookup_is_not_retried(mock_subprocess_run, auth_status):
    auth_status.answer["raise"] = FileNotFoundError("claude")
    for _ in range(3):
        assert llm.billing(_call(mock_subprocess_run)) == "unknown"
    assert len(auth_status) == 1


# ------------------------------------------------------------ consumers


def _resp(source):
    return llm.LLMResponse(text="", total_cost_usd=0.5, input_tokens=1,
                           output_tokens=1, api_key_source=source, raw={})


def _summary(*sources):
    summary = extract_mod.ExtractionSummary()
    for source in sources:
        summary.total_cost_usd += 0.5
        extract_mod._count_billing(summary, _resp(source))
    return summary


def test_extraction_counts_each_kind():
    summary = _summary("none", "ANTHROPIC_API_KEY", None, None)
    assert summary.all_calls_subscription is False
    assert (summary.api_calls, summary.unknown_billing_calls) == (1, 2)


def test_cost_line_subscription_is_an_equivalent_not_a_charge():
    line = extract_cmd._cost_line(_summary("none", "none"), 30)
    assert "no charge" in line and "≈$1.0000 API-price equivalent" in line


def test_cost_line_unknown_is_not_printed_as_charged():
    line = extract_cmd._cost_line(_summary("none", None), 30)
    assert line.startswith("≈$1.0000 at API prices (billing unknown)")


def test_cost_line_api_is_a_charge():
    line = extract_cmd._cost_line(_summary("user", None), 30)
    assert line == "$1.0000 (30 tokens)"
