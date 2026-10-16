"""The ``grok-cli`` provider: ``grok --prompt-file`` behind ``llm.resolve``."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mnemo.core import llm


@pytest.fixture(autouse=True)
def _clean_agent_env(monkeypatch):
    for name in ("MNEMO_AGENT", "GROK_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)


REPLY = {
    "text": "pong", "stopReason": "end_turn", "sessionId": "x",
    "usage": {"input_tokens": 100, "cache_read_input_tokens": 20,
              "cache_creation_input_tokens": 3, "output_tokens": 7},
    "total_cost_usd": 0.03,
}


def _ok(payload=REPLY):
    return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")


def _capture(monkeypatch, results):
    seen = []
    queue = list(results)

    def run(argv, **kw):
        # The prompt file exists while grok runs and holds the prompt.
        seen.append({"argv": argv, "kw": kw,
                     "prompt": Path(argv[argv.index("--prompt-file") + 1]).read_text(
                         encoding="utf-8")})
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(llm, "_subprocess_run", run)
    return seen


def test_registered_under_the_contract_name():
    assert "grok-cli" in llm.PROVIDERS
    assert llm.resolve({"extraction": {"provider": "grok-cli"}}) is llm.PROVIDERS["grok-cli"]


@pytest.mark.parametrize("cfg", [None, {}, {"extraction": {"provider": "claude-cli"}}])
def test_claude_stays_the_default_without_a_grok_signal(cfg):
    assert llm.resolve(cfg) is llm.PROVIDERS["claude-cli"]


@pytest.mark.parametrize("env", [{"MNEMO_AGENT": "grok"}, {"GROK_SESSION_ID": "abc"}])
@pytest.mark.parametrize("cfg", [None, {}, {"extraction": {"provider": ""}},
                                 {"extraction": {"provider": "claude-cli"}}])
def test_unset_provider_follows_the_current_cli(monkeypatch, env, cfg):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert llm.resolve(cfg) is llm.PROVIDERS["grok-cli"]


def test_grok_pin_holds_on_a_claude_machine(monkeypatch):
    monkeypatch.setenv("MNEMO_AGENT", "claude")
    assert llm.resolve({"extraction": {"provider": "grok-cli"}}) is llm.PROVIDERS["grok-cli"]


def test_call_argv_env_and_response(monkeypatch):
    seen = _capture(monkeypatch, [_ok()])
    resp = llm.PROVIDERS["grok-cli"]("héllo ação", system="be terse",
                                     model="claude-haiku-4-5", timeout=9)
    argv = seen[0]["argv"]
    assert argv[1:3] == ["--prompt-file", argv[2]]
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--system-prompt-override") + 1] == "be terse"
    assert "--model" not in argv  # a Claude id is not a Grok model
    assert "héllo ação" not in argv  # the prompt is in the file, not argv
    assert seen[0]["prompt"] == "héllo ação"
    assert seen[0]["kw"]["timeout"] == 9
    env = seen[0]["kw"]["env"]
    assert env["GROK_CLAUDE_HOOKS_ENABLED"] == "0"
    assert env["MNEMO_HOOKS_OFF"] == "1"
    assert not Path(argv[2]).exists()  # cleaned up
    assert resp.text == "pong"
    assert resp.provider == "grok-cli"
    assert resp.input_tokens == 123
    assert resp.output_tokens == 7
    assert resp.total_cost_usd == 0.03
    assert resp.api_key_source is None
    assert llm.billing(resp) == "unknown"


def test_grok_model_name_is_passed_and_no_system_means_no_flag(monkeypatch):
    seen = _capture(monkeypatch, [_ok()])
    llm.PROVIDERS["grok-cli"]("p", system=None, model="grok-4.7", timeout=5)
    argv = seen[0]["argv"]
    assert argv[argv.index("--model") + 1] == "grok-4.7"
    assert "--system-prompt-override" not in argv


def test_missing_binary_is_a_subprocess_error(monkeypatch):
    _capture(monkeypatch, [FileNotFoundError()])
    with pytest.raises(llm.LLMSubprocessError, match="grok CLI not found"):
        llm.PROVIDERS["grok-cli"]("p", system=None, model="m", timeout=5)


def test_timeout_retries_once_then_raises(monkeypatch):
    boom = subprocess.TimeoutExpired("grok", 5)
    seen = _capture(monkeypatch, [boom, _ok()])
    assert llm.PROVIDERS["grok-cli"]("p", system=None, model="m", timeout=5).text == "pong"
    assert len(seen) == 2
    _capture(monkeypatch, [boom, boom])
    with pytest.raises(llm.LLMTimeoutError):
        llm.PROVIDERS["grok-cli"]("p", system=None, model="m", timeout=5)


def test_rate_limit_retries_once(monkeypatch):
    limited = SimpleNamespace(returncode=1, stdout="", stderr="429 rate limit")
    seen = _capture(monkeypatch, [limited, _ok()])
    assert llm.PROVIDERS["grok-cli"]("p", system=None, model="m", timeout=5).text == "pong"
    assert len(seen) == 2


def test_nonzero_exit_raises_with_stderr(monkeypatch):
    _capture(monkeypatch, [SimpleNamespace(returncode=2, stdout="", stderr="not logged in")])
    with pytest.raises(llm.LLMSubprocessError, match="code 2: not logged in"):
        llm.PROVIDERS["grok-cli"]("p", system=None, model="m", timeout=5)


@pytest.mark.parametrize("stdout", ["not json", "[1]", json.dumps({"stopReason": "x"}),
                                    json.dumps({"text": None})])
def test_bad_envelope_is_an_envelope_error(monkeypatch, stdout):
    _capture(monkeypatch, [SimpleNamespace(returncode=0, stdout=stdout, stderr="")])
    with pytest.raises(llm.LLMEnvelopeError):
        llm.PROVIDERS["grok-cli"]("p", system=None, model="m", timeout=5)


def test_missing_usage_is_unknown_not_zero(monkeypatch):
    _capture(monkeypatch, [_ok({"text": "t"})])
    resp = llm.PROVIDERS["grok-cli"]("p", system=None, model="m", timeout=5)
    assert (resp.input_tokens, resp.output_tokens, resp.total_cost_usd) == (None, None, None)
