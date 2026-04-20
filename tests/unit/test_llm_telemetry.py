"""record_llm_call writes a structured telemetry entry."""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core.llm import LLMResponse
from mnemo.core.mcp import access_log


def _read_log(vault: Path) -> list[dict]:
    log = vault / ".mnemo" / "mcp-access-log.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


def test_record_llm_call_writes_entry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1_048_576),
    )
    response = LLMResponse(
        text="hello",
        total_cost_usd=0.001,
        input_tokens=1234,
        output_tokens=56,
        api_key_source="oauth",
        raw={},
    )
    access_log.record_llm_call(
        tmp_path,
        response,
        purpose="briefing",
        model="claude-haiku-4-5",
        project="myproj",
        agent="myproj",
        elapsed_ms=2345.6,
    )
    entries = _read_log(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert e["tool"] == "llm.call"
    assert e["purpose"] == "briefing"
    assert e["model"] == "claude-haiku-4-5"
    assert e["project"] == "myproj"
    assert e["agent"] == "myproj"
    assert e["usage"] == {"input_tokens": 1234, "output_tokens": 56}
    assert e["elapsed_ms"] == 2345.6
    assert "timestamp" in e and e["timestamp"].endswith("Z")
    assert e["result_count"] == 1  # so access_log_summary.is_well_formed accepts it


def test_record_llm_call_handles_missing_usage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1_048_576),
    )
    response = LLMResponse(
        text="hi",
        total_cost_usd=None,
        input_tokens=None,
        output_tokens=None,
        api_key_source=None,
        raw={},
    )
    access_log.record_llm_call(
        tmp_path,
        response,
        purpose="consolidation:feedback",
        model="claude-haiku-4-5",
        project=None,
        agent="myagent",
        elapsed_ms=100.0,
    )
    entries = _read_log(tmp_path)
    assert len(entries) == 1
    assert entries[0]["usage"] == {"input_tokens": 0, "output_tokens": 0}


def test_record_session_start_inject_writes_entry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1_048_576),
    )
    access_log.record_session_start_inject(
        tmp_path,
        envelope_bytes=4321,
        included_briefing=True,
        project="myproj",
        agent="myproj",
    )
    entries = _read_log(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert e["tool"] == "session_start.inject"
    assert e["envelope_bytes"] == 4321
    assert e["included_briefing"] is True
    assert e["project"] == "myproj"
    assert e["result_count"] == 1
