"""generate_session_briefing logs an llm.call entry on success."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from mnemo.core import briefing
from mnemo.core.llm import LLMResponse


def _fake_jsonl(path: Path) -> Path:
    path.write_text(json.dumps({
        "type": "user",
        "timestamp": "2026-04-20T12:00:00Z",
        "message": {
            "content": [{"type": "tool_use", "name": "Edit"}],
        },
    }) + "\n", encoding="utf-8")
    return path


def test_briefing_writes_telemetry_entry(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = {"vaultRoot": str(vault), "extraction": {"model": "claude-haiku-4-5"}}
    jsonl = _fake_jsonl(tmp_path / "session.jsonl")

    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1_048_576),
    )

    fake_response = LLMResponse(
        text="briefing body",
        total_cost_usd=0.001,
        input_tokens=500,
        output_tokens=100,
        api_key_source=None,
        raw={},
    )
    # Mock via monkeypatch on the imported module attribute (matches
    # tests/unit/test_briefing.py:46 convention; works correctly because
    # briefing.py does `from mnemo.core import llm` then `llm.call(...)`).
    monkeypatch.setattr("mnemo.core.briefing.llm.call", lambda *a, **kw: fake_response)
    out = briefing.generate_session_briefing(jsonl, agent="myagent", cfg=cfg)
    assert out is not None

    log = vault / ".mnemo" / "mcp-access-log.jsonl"
    entries = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    llm_entries = [e for e in entries if e.get("tool") == "llm.call"]
    assert len(llm_entries) == 1
    e = llm_entries[0]
    assert e["purpose"] == "briefing"
    assert e["agent"] == "myagent"
    assert e["usage"]["input_tokens"] == 500
    assert e["usage"]["output_tokens"] == 100
