"""Tests for telemetry_doctor — scan + open PR for telemetry anomalies."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mnemo.autopilot.selffix.telemetry_doctor import (
    TelemetryAnomaly,
    open_telemetry_fix_pr,
    scan_telemetry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_access_log(tmp_path: Path, entries: list) -> None:
    log_path = tmp_path / ".mnemo" / "mcp-access-log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _write_reflex_log(tmp_path: Path, entries: list) -> None:
    log_path = tmp_path / ".mnemo" / "reflex-log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


# ---------------------------------------------------------------------------
# TelemetryAnomaly dataclass
# ---------------------------------------------------------------------------


def test_telemetry_anomaly_has_required_fields() -> None:
    a = TelemetryAnomaly(
        kind="cost_usd_always_zero",
        detail="llm.call cost_usd is always 0 in 50 entries",
        affected_count=50,
    )
    assert a.kind == "cost_usd_always_zero"
    assert a.affected_count == 50


# ---------------------------------------------------------------------------
# scan_telemetry — no log
# ---------------------------------------------------------------------------


def test_scan_telemetry_returns_empty_when_no_log(tmp_path: Path) -> None:
    anomalies = scan_telemetry(vault_root=tmp_path)
    assert anomalies == []


# ---------------------------------------------------------------------------
# scan_telemetry — cost_usd_always_zero
# ---------------------------------------------------------------------------


def test_scan_telemetry_detects_cost_usd_always_zero(tmp_path: Path) -> None:
    entries = [
        {"ts": "2026-04-30T10:00:00Z", "event": "llm.call", "cost_usd": 0}
        for _ in range(10)
    ]
    _write_access_log(tmp_path, entries)
    anomalies = scan_telemetry(vault_root=tmp_path)
    kinds = [a.kind for a in anomalies]
    assert "cost_usd_always_zero" in kinds


def test_scan_telemetry_no_anomaly_when_cost_nonzero(tmp_path: Path) -> None:
    entries = [
        {"ts": "2026-04-30T10:00:00Z", "event": "llm.call", "cost_usd": 0.005}
        for _ in range(5)
    ] + [
        {"ts": "2026-04-30T10:00:00Z", "event": "llm.call", "cost_usd": 0}
        for _ in range(2)
    ]
    _write_access_log(tmp_path, entries)
    anomalies = scan_telemetry(vault_root=tmp_path)
    kinds = [a.kind for a in anomalies]
    assert "cost_usd_always_zero" not in kinds


def test_scan_telemetry_ignores_entries_without_llm_call_event(tmp_path: Path) -> None:
    entries = [
        {"ts": "2026-04-30T10:00:00Z", "event": "mcp.read", "cost_usd": 0}
        for _ in range(10)
    ]
    _write_access_log(tmp_path, entries)
    # non-llm.call entries with zero cost should NOT trigger the anomaly
    # (they don't have a cost_usd field in normal usage)
    anomalies = scan_telemetry(vault_root=tmp_path)
    # We only look at llm.call events — 0 such events means no anomaly
    assert anomalies == []


# ---------------------------------------------------------------------------
# scan_telemetry — prompt_tokens_null
# ---------------------------------------------------------------------------


def test_scan_telemetry_detects_prompt_tokens_null(tmp_path: Path) -> None:
    entries = [
        {"ts": "2026-04-30T10:00:00Z", "event": "llm.call", "cost_usd": 0.01, "prompt_tokens": None}
        for _ in range(5)
    ]
    _write_access_log(tmp_path, entries)
    anomalies = scan_telemetry(vault_root=tmp_path)
    kinds = [a.kind for a in anomalies]
    assert "prompt_tokens_null" in kinds


def test_scan_telemetry_no_prompt_tokens_anomaly_when_below_threshold(tmp_path: Path) -> None:
    """Only flag prompt_tokens_null when null rate exceeds threshold."""
    entries = (
        [{"ts": "2026-04-30T10:00:00Z", "event": "llm.call", "cost_usd": 0.01,
          "prompt_tokens": None}]
        + [{"ts": "2026-04-30T10:00:00Z", "event": "llm.call", "cost_usd": 0.01,
            "prompt_tokens": 100}
           for _ in range(20)]
    )
    _write_access_log(tmp_path, entries)
    anomalies = scan_telemetry(vault_root=tmp_path)
    kinds = [a.kind for a in anomalies]
    assert "prompt_tokens_null" not in kinds


# ---------------------------------------------------------------------------
# open_telemetry_fix_pr
# ---------------------------------------------------------------------------


def test_open_telemetry_fix_pr_dry_run(tmp_path: Path) -> None:
    anomalies = [TelemetryAnomaly(kind="cost_usd_always_zero", detail="x", affected_count=5)]
    (tmp_path / ".mnemo").mkdir(exist_ok=True)
    (tmp_path / ".mnemo" / "autopilot.json").write_text(
        json.dumps({"schema_version": 1, "state": "on", "paused_until": None,
                    "last_changed_at": None, "last_changed_by": None})
    )
    with patch("mnemo.autopilot.selffix.telemetry_doctor._gh.open_pr") as mock_pr:
        result = open_telemetry_fix_pr(
            anomalies, vault_root=tmp_path, repo_root=tmp_path, dry_run=True
        )
    mock_pr.assert_not_called()
    assert result is None


def test_open_telemetry_fix_pr_budget_exhausted(tmp_path: Path) -> None:
    anomalies = [TelemetryAnomaly(kind="cost_usd_always_zero", detail="x", affected_count=5)]
    with patch(
        "mnemo.autopilot.selffix.telemetry_doctor.pr_budget.can_open",
        return_value=(False, "daily cap"),
    ):
        result = open_telemetry_fix_pr(anomalies, vault_root=tmp_path, repo_root=tmp_path)
    assert result is None


def test_open_telemetry_fix_pr_opens_draft_pr(tmp_path: Path) -> None:
    anomalies = [TelemetryAnomaly(kind="cost_usd_always_zero", detail="x", affected_count=5)]
    (tmp_path / ".mnemo").mkdir(exist_ok=True)
    (tmp_path / ".mnemo" / "autopilot.json").write_text(
        json.dumps({"schema_version": 1, "state": "on", "paused_until": None,
                    "last_changed_at": None, "last_changed_by": None})
    )
    with patch("mnemo.autopilot.selffix.telemetry_doctor._gh.create_branch", return_value="b"), \
         patch("mnemo.autopilot.selffix.telemetry_doctor._gh.push_branch", return_value=True), \
         patch("mnemo.autopilot.selffix.telemetry_doctor._gh.open_pr", return_value=77) as mock_pr, \
         patch("mnemo.autopilot.selffix.telemetry_doctor.pr_budget.record_opened") as mock_rec:
        result = open_telemetry_fix_pr(anomalies, vault_root=tmp_path, repo_root=tmp_path)

    assert result == 77
    mock_rec.assert_called_once()
    # Must be opened as draft
    call_kwargs = mock_pr.call_args[1]
    assert call_kwargs.get("draft") is True
