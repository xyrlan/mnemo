"""Tests for mnemo.core.mcp.access_log — MCP call telemetry writer."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from mnemo.core.mcp.access_log import record


def _make_entry(**overrides) -> dict:
    base = {
        "timestamp": "2026-04-16T12:00:00Z",
        "tool": "list_rules_by_topic",
        "args": {"topic": "git"},
        "scope_requested": "project",
        "scope_effective": "project",
        "project": "mnemo",
        "result_count": 2,
        "hit_slugs": ["rule-a", "rule-b"],
        "elapsed_ms": 5.3,
    }
    base.update(overrides)
    return base


def test_record_appends_valid_jsonl(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1_048_576),
    )
    entry = _make_entry()
    record(vault, entry)
    record(vault, _make_entry(tool="read_mnemo_rule"))

    log_path = vault / ".mnemo" / "mcp-access-log.jsonl"
    assert log_path.exists()
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 2
    parsed = json.loads(lines[0])
    assert parsed["tool"] == "list_rules_by_topic"
    assert parsed["hit_slugs"] == ["rule-a", "rule-b"]


def test_record_creates_mnemo_dir_if_missing(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1_048_576),
    )
    record(vault, _make_entry())
    assert (vault / ".mnemo" / "mcp-access-log.jsonl").exists()


def test_record_telemetry_off_no_write(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (False, 1_048_576),
    )
    record(vault, _make_entry())
    assert not (vault / ".mnemo" / "mcp-access-log.jsonl").exists()


def test_record_rotation_integration(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    log_path = vault / ".mnemo" / "mcp-access-log.jsonl"
    log_path.write_text("x" * 2000)
    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1000),
    )
    record(vault, _make_entry())
    assert log_path.exists()
    assert log_path.with_suffix(".jsonl.1").exists()


def test_record_unserializable_entry_silent(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1_048_576),
    )
    record(vault, {"bad": object()})
    log_path = vault / ".mnemo" / "mcp-access-log.jsonl"
    assert not log_path.exists()


def test_record_unwritable_vault_silent(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1_048_576),
    )
    record(vault, _make_entry())


def test_record_truncates_long_string_values(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1_048_576),
    )
    entry = _make_entry(args={"topic": "a" * 2000})
    record(vault, entry)
    log_path = vault / ".mnemo" / "mcp-access-log.jsonl"
    parsed = json.loads(log_path.read_text().strip())
    assert len(parsed["args"]["topic"]) <= 1030


def test_record_entry_schema_has_required_fields(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1_048_576),
    )
    record(vault, _make_entry())
    log_path = vault / ".mnemo" / "mcp-access-log.jsonl"
    parsed = json.loads(log_path.read_text().strip())
    for key in ("timestamp", "tool", "args", "scope_requested",
                "scope_effective", "project", "result_count",
                "hit_slugs", "elapsed_ms"):
        assert key in parsed, f"missing key: {key}"
