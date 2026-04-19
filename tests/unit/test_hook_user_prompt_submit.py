"""UserPromptSubmit retrieval flow + silence reasons + dedupe."""
from __future__ import annotations

import io
import json
from unittest.mock import patch

from mnemo.hooks import user_prompt_submit as hook


def _run_hook(stdin_payload: dict) -> tuple[int, str]:
    out = io.StringIO()
    with patch("sys.stdin", io.StringIO(json.dumps(stdin_payload))), \
         patch("sys.stdout", out):
        rc = hook.main()
    return rc, out.getvalue()


def test_hook_returns_silence_on_disabled_reflex(tmp_vault, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "reflex": {"enabled": False},
    }))

    rc, stdout = _run_hook({
        "cwd": str(tmp_vault),
        "session_id": "sid-xyz",
        "prompt": "Use Prisma mock for the new test",
    })
    assert rc == 0
    assert stdout == ""


def test_hook_returns_silence_on_short_prompt(tmp_vault, monkeypatch):
    # Pre-gate: < 3 distinct non-stopword tokens.
    _enable_reflex(tmp_vault, monkeypatch)
    rc, stdout = _run_hook({
        "cwd": str(tmp_vault), "session_id": "sid", "prompt": "ok",
    })
    assert rc == 0 and stdout == ""


def test_hook_emits_on_confident_match(tmp_vault, monkeypatch, synthetic_index):
    """Uses fixture that writes a reflex-index.json whose top match is 'use-prisma-mock'."""
    _enable_reflex(tmp_vault, monkeypatch)
    synthetic_index(tmp_vault)

    rc, stdout = _run_hook({
        "cwd": str(tmp_vault),
        "session_id": "sid-1",
        "prompt": "How do I mock prisma in a jest test with typescript",
    })
    assert rc == 0
    payload = json.loads(stdout)
    text = payload["hookSpecificOutput"]["additionalContext"]
    assert "mnemo reflex context:" in text
    assert "[[use-prisma-mock]]" in text


def _enable_reflex(vault, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(vault / "mnemo.config.json"))
    (vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(vault),
        "reflex": {"enabled": True},
    }))
