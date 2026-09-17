"""Tests for telemetry_doctor — scan + open PR for telemetry anomalies."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

from mnemo.autopilot.selffix.telemetry_doctor import (
    TelemetryAnomaly,
    open_telemetry_fix_pr,
    scan_telemetry,
)
from mnemo.core.llm import LLMResponse
from mnemo.core.mcp import access_log


@pytest.fixture(autouse=True)
def _network_on(monkeypatch):
    """These tests exercise the network path, which is off by default.

    ``autopilot.network.enabled`` gates every ``gh`` call site; turning it on
    here keeps this module testing what it was written to test. The gate's own
    coverage lives in ``tests/unit/test_autopilot_network_gate.py``.
    """
    from mnemo.autopilot.core import network

    monkeypatch.setattr(network, "enabled", lambda cfg=None: True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _telemetry_on(monkeypatch):
    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1_048_576),
    )


def _log_calls(
    vault: Path, n: int, *, cost: Optional[float] = 0.002,
    input_tokens: Optional[int] = 1200,
) -> None:
    """Append ``n`` rows through the production writer — never a hand-built
    dict, whose shape is what hid #370."""
    for _ in range(n):
        access_log.record_llm_call(
            vault,
            LLMResponse(
                text="x", total_cost_usd=cost, input_tokens=input_tokens,
                output_tokens=40, api_key_source="none", raw={},
            ),
            purpose="extraction", model="claude-haiku-4-5",
            project="p", agent="p", elapsed_ms=10.0,
        )


def _log_path(vault: Path) -> Path:
    return vault / ".mnemo" / "mcp-access-log.jsonl"


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


def test_the_writer_row_is_what_the_doctor_selects(tmp_path: Path) -> None:
    """#370: the doctor selected on ``event`` while the writer writes
    ``tool``, so no real row ever reached a check."""
    _log_calls(tmp_path, 1)
    row = json.loads(_log_path(tmp_path).read_text(encoding="utf-8"))
    assert row["tool"] == "llm.call"
    assert "event" not in row
    assert row["cost_usd"] == 0.002
    assert row["usage"]["input_tokens"] == 1200


def test_scan_telemetry_detects_cost_usd_always_zero(tmp_path: Path) -> None:
    _log_calls(tmp_path, 10, cost=0.0)
    kinds = [a.kind for a in scan_telemetry(vault_root=tmp_path)]
    assert "cost_usd_always_zero" in kinds


def test_scan_telemetry_detects_cost_never_reported(tmp_path: Path) -> None:
    """A CLI result without ``total_cost_usd`` lands as ``cost_usd: null``."""
    _log_calls(tmp_path, 6, cost=None)
    flagged = [a for a in scan_telemetry(vault_root=tmp_path)
               if a.kind == "cost_usd_always_zero"]
    assert len(flagged) == 1
    assert flagged[0].affected_count == 6


def test_scan_telemetry_counts_entries_in_the_rotated_file(tmp_path: Path) -> None:
    """#140: entries split across ``.1`` and the live file must be pooled —
    three in the rotated file plus two live clears the minimum and flags."""
    _log_calls(tmp_path, 3, cost=0.0)
    _log_path(tmp_path).rename(tmp_path / ".mnemo" / "mcp-access-log.jsonl.1")
    _log_calls(tmp_path, 2, cost=0.0)
    flagged = [a for a in scan_telemetry(vault_root=tmp_path)
               if a.kind == "cost_usd_always_zero"]
    assert len(flagged) == 1
    assert flagged[0].affected_count == 5


def test_scan_telemetry_no_anomaly_when_cost_nonzero(tmp_path: Path) -> None:
    _log_calls(tmp_path, 5, cost=0.005)
    _log_calls(tmp_path, 2, cost=0.0)
    assert scan_telemetry(vault_root=tmp_path) == []


def test_rows_from_before_cost_was_recorded_are_not_flagged(tmp_path: Path) -> None:
    """Rows written before #370 have no ``cost_usd`` key — absence of the key
    is history, not a zero cost."""
    _log_calls(tmp_path, 8)
    log = _log_path(tmp_path)
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    for r in rows:
        del r["cost_usd"]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    _log_calls(tmp_path, 2, cost=0.0)  # too few new rows to judge
    assert scan_telemetry(vault_root=tmp_path) == []


def test_scan_telemetry_ignores_other_tools(tmp_path: Path) -> None:
    access_log.record(tmp_path, {
        "timestamp": "2026-09-17T10:00:00Z", "tool": "read_mnemo_rule",
        "agent": "p", "project": "p", "result_count": 1, "elapsed_ms": 1.0,
    })
    _log_calls(tmp_path, 4, cost=0.0)  # one short of the minimum
    assert scan_telemetry(vault_root=tmp_path) == []


# ---------------------------------------------------------------------------
# scan_telemetry — input_tokens_zero
# ---------------------------------------------------------------------------


def test_scan_telemetry_detects_input_tokens_missing(tmp_path: Path) -> None:
    """The writer records an unreported count as 0 under ``usage``."""
    _log_calls(tmp_path, 5, input_tokens=None)
    flagged = [a for a in scan_telemetry(vault_root=tmp_path)
               if a.kind == "input_tokens_zero"]
    assert len(flagged) == 1
    assert flagged[0].affected_count == 5


def test_scan_telemetry_no_input_tokens_anomaly_when_below_threshold(tmp_path: Path) -> None:
    _log_calls(tmp_path, 1, input_tokens=None)
    _log_calls(tmp_path, 20)
    kinds = [a.kind for a in scan_telemetry(vault_root=tmp_path)]
    assert "input_tokens_zero" not in kinds


# ---------------------------------------------------------------------------
# open_telemetry_fix_pr
# ---------------------------------------------------------------------------


def test_open_telemetry_fix_pr_dry_run(tmp_path: Path) -> None:
    anomalies = [TelemetryAnomaly(kind="cost_usd_always_zero", detail="x", affected_count=5)]
    (tmp_path / ".mnemo").mkdir(exist_ok=True)
    (tmp_path / ".mnemo" / "autopilot.json").write_text(
        json.dumps({"schema_version": 1, "state": "on", "paused_until": None,
                    "last_changed_at": None, "last_changed_by": None}), encoding="utf-8"
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


def test_open_telemetry_fix_pr_opens_issue(tmp_path: Path) -> None:
    """An anomaly report has no diff, so it is filed as an issue, not a PR."""
    anomalies = [TelemetryAnomaly(kind="cost_usd_always_zero", detail="x", affected_count=5)]
    (tmp_path / ".mnemo").mkdir(exist_ok=True)
    (tmp_path / ".mnemo" / "autopilot.json").write_text(
        json.dumps({"schema_version": 1, "state": "on", "paused_until": None,
                    "last_changed_at": None, "last_changed_by": None}), encoding="utf-8"
    )
    with patch("mnemo.autopilot.selffix.telemetry_doctor._gh.open_issue", return_value=77) as mock_issue, \
         patch("mnemo.autopilot.selffix.telemetry_doctor.pr_budget.record_opened") as mock_rec:
        result = open_telemetry_fix_pr(anomalies, vault_root=tmp_path, repo_root=tmp_path)

    assert result == 77
    mock_rec.assert_called_once()
    assert mock_issue.call_args.kwargs["labels"] == ["mnemo:self-fix"]
