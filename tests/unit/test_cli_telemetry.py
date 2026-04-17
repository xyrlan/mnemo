"""Tests for `mnemo telemetry` CLI command."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo import cli


def _write_log(vault: Path, entries: list[dict]) -> None:
    mnemo_dir = vault / ".mnemo"
    mnemo_dir.mkdir(parents=True, exist_ok=True)
    path = mnemo_dir / "mcp-access-log.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def _sample_entry(**overrides) -> dict:
    base = {
        "timestamp": "2026-04-16T12:00:00Z",
        "tool": "list_rules_by_topic",
        "args": {"topic": "git", "scope": "project"},
        "scope_requested": "project",
        "scope_effective": "project",
        "project": "mnemo",
        "result_count": 3,
        "hit_slugs": ["a", "b", "c"],
        "elapsed_ms": 1.4,
    }
    base.update(overrides)
    return base


def test_telemetry_registered_in_help(capsys: pytest.CaptureFixture):
    rc = cli.main(["help"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "telemetry" in captured.out


def test_telemetry_prints_human_summary(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture):
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_log(vault, [
        _sample_entry(project="mnemo", result_count=3),
        _sample_entry(project="bingx-robot", result_count=0),
    ])
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    rc = cli.main(["telemetry"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Total calls: 2" in captured.out
    assert "mnemo" in captured.out
    assert "bingx-robot" in captured.out


def test_telemetry_json_flag_outputs_json(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture):
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_log(vault, [_sample_entry(project="mnemo", result_count=1)])
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    rc = cli.main(["telemetry", "--json"])
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["total_calls"] == 1
    assert "mnemo" in payload["by_project"]


def test_telemetry_empty_log_returns_zero(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    rc = cli.main(["telemetry"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Total calls: 0" in captured.out
