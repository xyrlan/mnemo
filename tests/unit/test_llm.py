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


def test_call_argv_uses_strict_mcp_config_to_strip_plugin_tools(mock_subprocess_run):
    """Issue #6: --tools "" alone does not strip MCP plugin tools loaded from
    the user's Claude Code config. --strict-mcp-config (without any paired
    --mcp-config) tells the CLI to ignore every MCP configuration source,
    producing an empty mcp_servers list and, combined with --tools "",
    an empty init.tools list. Unlike --bare, it preserves OAuth/keychain
    auth so subscription users are not broken.
    """
    mock_subprocess_run([MockCompletedProcess(stdout=_envelope('{}'))])
    llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)
    argv = mock_subprocess_run.calls[0]["argv"]
    assert "--strict-mcp-config" in argv, (
        "--strict-mcp-config must be in argv to suppress MCP plugin tools "
        "while preserving subscription auth"
    )
    # Must NOT use --bare: it breaks OAuth/keychain auth for subscription users
    assert "--bare" not in argv, (
        "--bare breaks OAuth/keychain auth; use --strict-mcp-config instead"
    )


def test_call_subprocess_env_disables_extended_thinking(mock_subprocess_run):
    """Issue #7: Haiku 4.5 does extended thinking by default in `claude --print`,
    costing ~200-400 output tokens and several seconds of wall-time on trivial
    prompts. The `CLAUDE_CODE_DISABLE_THINKING=1` environment variable is the
    canonical switch to strip the thinking block entirely.
    """
    mock_subprocess_run([MockCompletedProcess(stdout=_envelope('{}'))])
    llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)
    kwargs = mock_subprocess_run.calls[0]["kwargs"]
    env = kwargs.get("env")
    assert env is not None, (
        "llm.call must pass an explicit env= to subprocess.run so we can "
        "inject CLAUDE_CODE_DISABLE_THINKING=1"
    )
    assert env.get("CLAUDE_CODE_DISABLE_THINKING") == "1", (
        "CLAUDE_CODE_DISABLE_THINKING=1 must be set in the subprocess env "
        "to suppress the thinking block on Haiku 4.5"
    )


def test_call_subprocess_env_preserves_parent_environment(mock_subprocess_run):
    """When injecting CLAUDE_CODE_DISABLE_THINKING, we must inherit the parent
    environment (PATH, HOME, auth-related vars, etc.) — otherwise the CLI
    cannot locate claude binaries or find the user's auth.
    """
    import os
    mock_subprocess_run([MockCompletedProcess(stdout=_envelope('{}'))])
    llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)
    env = mock_subprocess_run.calls[0]["kwargs"].get("env") or {}
    assert "PATH" in env, "subprocess env must inherit PATH from parent"
    # A specific parent var used as a canary — $HOME is reliably present
    assert env.get("HOME") == os.environ.get("HOME"), (
        "subprocess env must inherit parent HOME"
    )


def test_call_omits_system_prompt_when_none(mock_subprocess_run):
    mock_subprocess_run([MockCompletedProcess(stdout=_envelope('{}'))])
    llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)
    argv = mock_subprocess_run.calls[0]["argv"]
    assert "--system-prompt" not in argv


def test_call_parses_array_envelope_from_claude_code_2x(mock_subprocess_run):
    """Claude Code CLI >=2.x returns --output-format json as an array of events."""
    array_envelope = json.dumps([
        {
            "type": "system",
            "subtype": "init",
            "apiKeySource": "none",
            "model": "claude-haiku-4-5",
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Hi"}]},
        },
        {
            "type": "result",
            "subtype": "success",
            "result": '{"pages": [{"slug": "x"}]}',
            "total_cost_usd": 0.0055,
            "usage": {"input_tokens": 2775, "output_tokens": 542},
        },
    ])
    mock_subprocess_run([MockCompletedProcess(stdout=array_envelope)])
    resp = llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)
    assert resp.text == '{"pages": [{"slug": "x"}]}'
    assert resp.total_cost_usd == 0.0055
    assert resp.input_tokens == 2775
    assert resp.output_tokens == 542
    assert resp.api_key_source == "none"


def test_call_array_envelope_missing_result_raises(mock_subprocess_run):
    array_envelope = json.dumps([
        {"type": "system", "subtype": "init", "apiKeySource": "none"},
        {"type": "assistant", "message": {}},
    ])
    mock_subprocess_run([MockCompletedProcess(stdout=array_envelope)])
    with pytest.raises(llm.LLMParseError, match="no result event"):
        llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)
