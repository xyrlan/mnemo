# tests/integration/test_hooks_never_raise.py
"""Critical test from spec § 10.3: hooks must never crash on malformed input."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from mnemo.hooks import session_start, session_end, user_prompt, post_tool_use

ALL_HOOKS = [session_start, session_end, user_prompt, post_tool_use]

MALFORMED_PAYLOADS = [
    # 1-10: invalid JSON shapes
    "",
    "{",
    "}",
    "null",
    "true",
    "[]",
    "[1,2,3]",
    '"a string"',
    "42",
    "{not even close",
    # 11-20: empty / minimal objects
    "{}",
    '{"session_id": null}',
    '{"session_id": ""}',
    '{"session_id": 12345}',  # numeric id
    '{"session_id": ["a","b"]}',
    '{"session_id": {"nested": true}}',
    '{"cwd": null}',
    '{"cwd": "/no/such/path/here/at/all"}',
    '{"prompt": null}',
    '{"prompt": ""}',
    # 21-30: edge content
    '{"prompt": "\\u0000"}',
    '{"prompt": "' + "x" * 50_000 + '"}',
    '{"prompt": "\\n\\n\\n"}',
    '{"prompt": "<system-reminder>x</system-reminder>"}',
    '{"prompt": "🦄🌈"}',
    '{"reason": "weird-reason"}',
    '{"source": ""}',
    '{"source": null}',
    '{"source": 42}',
    '{"tool_name": "Bash"}',  # not Write/Edit
    # 31-40: tool_input/tool_response shapes
    '{"tool_name": "Edit", "tool_input": null}',
    '{"tool_name": "Edit", "tool_input": []}',
    '{"tool_name": "Edit", "tool_input": "string"}',
    '{"tool_name": "Edit", "tool_input": {"file_path": null}}',
    '{"tool_name": "Edit", "tool_input": {"file_path": 12345}}',
    '{"tool_name": "Edit", "tool_input": {"file_path": ""}}',
    '{"tool_name": "Edit", "tool_response": null}',
    '{"tool_name": "Edit", "tool_response": "fail"}',
    '{"tool_name": "Edit", "tool_response": {"filePath": null}}',
    '{"tool_name": "Write", "tool_response": {"filePath": "/etc/passwd"}}',
    # 41-50: combinations of partial data
    '{"session_id": "x", "cwd": "/", "prompt": "ok"}',
    '{"session_id": "x", "cwd": null, "prompt": null}',
    '{"session_id": "x", "tool_name": "Edit", "tool_input": {"file_path": "/tmp/x"}}',
    '{"session_id": "abc", "reason": null}',
    '{"session_id": "abc", "source": "resume", "cwd": "/"}',
    '{"session_id": "abc", "tool_name": "Edit"}',
    '{"session_id": "x".*}',  # garbage
    '\\xff\\xfe binary',
    '{"prompt": ' + json.dumps("a" * 5000) + '}',
    '{"session_id": "ok", "cwd": "/tmp", "tool_name": "Write", "tool_input": {"file_path": "/tmp/x"}, "tool_response": {"filePath": "/tmp/x"}}',
]


@pytest.mark.parametrize("hook", ALL_HOOKS)
@pytest.mark.parametrize("payload", MALFORMED_PAYLOADS)
def test_hook_never_raises(
    hook,
    payload: str,
    tmp_vault: Path,
    tmp_home: Path,
    tmp_tempdir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    rc = hook.main()
    assert rc == 0, f"hook {hook.__name__} returned {rc} on payload {payload[:80]!r}"


def test_circuit_breaker_short_circuits_all_hooks(
    tmp_vault: Path,
    tmp_home: Path,
    tmp_tempdir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Critical test § 10.3: test_circuit_breaker_threshold."""
    from mnemo.core import errors
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    for i in range(15):
        try:
            raise ValueError(f"e{i}")
        except ValueError as e:
            errors.log_error(tmp_vault, "test", e)
    assert not errors.should_run(tmp_vault)
    valid_payload = json.dumps({"session_id": "x", "cwd": "/tmp", "prompt": "hello"})
    for hook in ALL_HOOKS:
        monkeypatch.setattr(sys, "stdin", io.StringIO(valid_payload))
        assert hook.main() == 0
