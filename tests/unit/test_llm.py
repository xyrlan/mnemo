"""Unit tests for core/llm.py — resilient subprocess JSON parsing."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mnemo.core import llm
from tests.conftest import MockCompletedProcess


def _envelope(result_text: str, *, cost: float | None = 0.0048, api_source: str = "none") -> str:
    env = {
        "type": "result",
        "result": result_text,
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "apiKeySource": api_source,
    }
    if cost is not None:
        env["total_cost_usd"] = cost
    return json.dumps(env)


def test_call_returns_response_on_clean_json(mock_subprocess_run):
    inner = '{"pages": [{"slug": "x", "body": "y"}]}'
    mock_subprocess_run([MockCompletedProcess(stdout=_envelope(inner))])
    resp = llm.call("prompt body", system="sys", model="claude-haiku-4-5", timeout=60)
    assert resp.text == inner
    assert resp.total_cost_usd == 0.0048
    assert resp.input_tokens == 100
    assert resp.output_tokens == 50
    assert resp.api_key_source == "none"


def test_call_strips_markdown_fences(mock_subprocess_run):
    inner = '```json\n{"pages": []}\n```'
    mock_subprocess_run([MockCompletedProcess(stdout=_envelope(inner))])
    resp = llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)
    parsed = llm._parse_llm_json(resp.text)
    assert parsed == {"pages": []}


def test_parse_llm_json_handles_prose_prefix():
    raw = 'Sure! Here is the JSON you asked for:\n\n```json\n{"pages": [{"slug": "a"}]}\n```\n\nAnything else?'
    obj = llm._parse_llm_json(raw)
    assert obj == {"pages": [{"slug": "a"}]}


def test_parse_llm_json_handles_trailing_garbage():
    raw = '{"pages": [{"slug": "a", "body": "hello"}]} and more commentary'
    obj = llm._parse_llm_json(raw)
    assert obj == {"pages": [{"slug": "a", "body": "hello"}]}


def test_parse_llm_json_empty_response_raises():
    with pytest.raises(llm.LLMParseError, match="no JSON object"):
        llm._parse_llm_json("")


def test_parse_llm_json_prose_only_raises():
    with pytest.raises(llm.LLMParseError, match="no JSON object"):
        llm._parse_llm_json("I could not extract anything useful.")


def test_parse_llm_json_malformed_raises_with_offset():
    with pytest.raises(llm.LLMParseError, match="offset"):
        llm._parse_llm_json('{"pages": [,]}')


def test_call_returns_none_tokens_when_usage_missing(mock_subprocess_run):
    env = json.dumps({
        "type": "result",
        "result": "{}",
        "apiKeySource": "none",
    })
    mock_subprocess_run([MockCompletedProcess(stdout=env)])
    resp = llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)
    assert resp.input_tokens is None
    assert resp.output_tokens is None
    assert resp.total_cost_usd is None


def test_call_propagates_api_key_source(mock_subprocess_run):
    mock_subprocess_run([MockCompletedProcess(stdout=_envelope('{}', api_source="user"))])
    resp = llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)
    assert resp.api_key_source == "user"


def test_call_raises_subprocess_error_on_nonzero_exit(mock_subprocess_run):
    mock_subprocess_run([MockCompletedProcess(stdout="", stderr="boom", returncode=1)])
    with pytest.raises(llm.LLMSubprocessError, match="boom"):
        llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)


def test_call_retries_once_on_rate_limit(mock_subprocess_run):
    mock_subprocess_run([
        MockCompletedProcess(stdout="", stderr="API Error: rate limit exceeded", returncode=1),
        MockCompletedProcess(stdout=_envelope('{}')),
    ])
    resp = llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)
    assert resp.api_key_source == "none"
    # Two subprocess invocations happened
    assert len(mock_subprocess_run.calls) == 2


def test_call_raises_after_second_rate_limit(mock_subprocess_run):
    mock_subprocess_run([
        MockCompletedProcess(stdout="", stderr="rate limit", returncode=1),
        MockCompletedProcess(stdout="", stderr="rate limit again", returncode=1),
    ])
    with pytest.raises(llm.LLMSubprocessError, match="rate limit"):
        llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)


def test_call_retries_once_on_timeout(mock_subprocess_run):
    mock_subprocess_run([
        subprocess.TimeoutExpired(cmd=["claude"], timeout=60),
        MockCompletedProcess(stdout=_envelope('{}')),
    ])
    resp = llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)
    assert resp.api_key_source == "none"


def test_call_raises_when_claude_cli_missing(mock_subprocess_run):
    mock_subprocess_run([FileNotFoundError("[Errno 2] No such file or directory: 'claude'")])
    with pytest.raises(llm.LLMSubprocessError, match="claude CLI not found"):
        llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)


def test_call_raises_parse_error_on_invalid_envelope(mock_subprocess_run):
    mock_subprocess_run([MockCompletedProcess(stdout="this is not json at all")])
    with pytest.raises(llm.LLMParseError):
        llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)


def test_call_builds_expected_argv(mock_subprocess_run):
    mock_subprocess_run([MockCompletedProcess(stdout=_envelope('{}'))])
    llm.call("prompt text", system="sys text", model="claude-haiku-4-5", timeout=60)
    argv = mock_subprocess_run.calls[0]["argv"]
    assert argv[0] == "claude"
    assert "--print" in argv
    assert "--no-session-persistence" in argv
    assert "--output-format" in argv and "json" in argv
    assert "--model" in argv and "claude-haiku-4-5" in argv
    assert "--tools" in argv
    assert "--system-prompt" in argv and "sys text" in argv
    # prompt piped via stdin, NOT argv
    assert "prompt text" not in argv
    assert mock_subprocess_run.calls[0]["input"] == "prompt text"


def test_call_omits_system_prompt_when_none(mock_subprocess_run):
    mock_subprocess_run([MockCompletedProcess(stdout=_envelope('{}'))])
    llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)
    argv = mock_subprocess_run.calls[0]["argv"]
    assert "--system-prompt" not in argv
