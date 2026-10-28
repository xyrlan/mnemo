"""A failed ``claude -p`` says why in ``.errors.log`` (#757).

With ``--output-format json`` the CLI reports an API failure in its stdout
envelope, exits 1, and leaves stderr empty — the shape below is what the real
CLI printed for an oversized briefing prompt, trimmed to the fields read.
"""
from __future__ import annotations

import json

import pytest

from mnemo.core import llm
from tests.conftest import MockCompletedProcess

_TOO_LONG = (
    "Prompt is too long · the request is ~747935 tokens (limit 200000) but this "
    "conversation is only ~595409 tokens"
)


def _error_envelope(message: str) -> str:
    return json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "api_error_status": 400,
        "terminal_reason": "prompt_too_long",
        "result": message,
    })


def test_empty_stderr_reports_the_envelope_message(mock_subprocess_run):
    mock_subprocess_run([
        MockCompletedProcess(stdout=_error_envelope(_TOO_LONG), stderr="", returncode=1),
    ])
    with pytest.raises(llm.LLMSubprocessError) as info:
        llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)
    assert str(info.value) == f"claude exited with code 1: {_TOO_LONG}"


def test_envelope_as_event_array_is_read_too(mock_subprocess_run):
    stdout = json.dumps([{"type": "system", "subtype": "init"}, json.loads(_error_envelope("boom"))])
    mock_subprocess_run([MockCompletedProcess(stdout=stdout, stderr="", returncode=1)])
    with pytest.raises(llm.LLMSubprocessError, match="code 1: boom$"):
        llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)


def test_stderr_is_kept_beside_the_envelope_message(mock_subprocess_run):
    mock_subprocess_run([
        MockCompletedProcess(
            stdout=_error_envelope("There's an issue with the selected model"),
            stderr="[claude-code:unrecognized_model] {}",
            returncode=1,
        ),
    ])
    with pytest.raises(llm.LLMSubprocessError) as info:
        llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)
    assert "issue with the selected model" in str(info.value)
    assert "unrecognized_model" in str(info.value)


def test_plain_stdout_stands_in_when_stderr_is_empty(mock_subprocess_run):
    mock_subprocess_run([MockCompletedProcess(stdout="Not logged in\n", stderr="", returncode=1)])
    with pytest.raises(llm.LLMSubprocessError, match="code 1: Not logged in$"):
        llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)


def test_detail_is_one_truncated_line(mock_subprocess_run):
    mock_subprocess_run([
        MockCompletedProcess(stdout="", stderr="line one\nline two\n" + "x" * 5000, returncode=1),
    ])
    with pytest.raises(llm.LLMSubprocessError) as info:
        llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)
    detail = str(info.value).split(": ", 1)[1]
    assert "\n" not in detail
    assert detail.startswith("line one line two")
    assert len(detail) == llm._FAILURE_DETAIL_MAX_CHARS
    assert detail.endswith("…")


def test_rate_limit_in_the_envelope_is_retried(mock_subprocess_run, monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
    ok = json.dumps({"type": "result", "result": "{}", "usage": {}, "apiKeySource": "none"})
    mock_subprocess_run([
        MockCompletedProcess(stdout=_error_envelope("API Error: Rate limit reached"), stderr="", returncode=1),
        MockCompletedProcess(stdout=ok),
    ])
    llm.call("p", system=None, model="claude-haiku-4-5", timeout=60)
    assert len(mock_subprocess_run.calls) == 2
