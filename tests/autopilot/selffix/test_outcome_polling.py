"""Tests for outcome_poller — poll closed self-fix PRs and record outcomes."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mnemo.autopilot.selffix.outcome_poller import poll_outcomes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gh_output(prs: list) -> MagicMock:
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps(prs)
    proc.stderr = ""
    return proc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_poll_outcomes_calls_record_outcome_for_merged_pr(tmp_path: Path) -> None:
    prs = [{"number": 42, "state": "MERGED"}]
    with patch("subprocess.run", side_effect=[_make_gh_output(prs), _make_gh_output([])]), \
         patch("mnemo.autopilot.selffix.outcome_poller.pr_budget.record_outcome") as mock_rec:
        count = poll_outcomes(vault_root=tmp_path)
    assert count == 1
    mock_rec.assert_called_once_with(
        vault_root=tmp_path, pr_number=42, outcome="merged"
    )


def test_poll_outcomes_calls_record_outcome_for_closed_pr(tmp_path: Path) -> None:
    prs = [{"number": 7, "state": "CLOSED"}]
    with patch("subprocess.run", side_effect=[_make_gh_output(prs), _make_gh_output([])]), \
         patch("mnemo.autopilot.selffix.outcome_poller.pr_budget.record_outcome") as mock_rec:
        poll_outcomes(vault_root=tmp_path)
    mock_rec.assert_called_once_with(
        vault_root=tmp_path, pr_number=7, outcome="closed"
    )


def test_poll_outcomes_multiple_prs(tmp_path: Path) -> None:
    prs = [
        {"number": 1, "state": "MERGED"},
        {"number": 2, "state": "CLOSED"},
        {"number": 3, "state": "CLOSED"},
    ]
    with patch("subprocess.run", side_effect=[_make_gh_output(prs), _make_gh_output([])]), \
         patch("mnemo.autopilot.selffix.outcome_poller.pr_budget.record_outcome") as mock_rec:
        count = poll_outcomes(vault_root=tmp_path)
    assert count == 3
    assert mock_rec.call_count == 3


def test_poll_outcomes_returns_zero_when_gh_unavailable(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError):
        count = poll_outcomes(vault_root=tmp_path)
    assert count == 0


def test_poll_outcomes_returns_zero_when_no_closed_prs(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_make_gh_output([])):
        count = poll_outcomes(vault_root=tmp_path)
    assert count == 0


def test_poll_outcomes_returns_zero_on_gh_error(tmp_path: Path) -> None:
    proc = MagicMock()
    proc.returncode = 1
    proc.stdout = ""
    proc.stderr = "error"
    with patch("subprocess.run", return_value=proc):
        count = poll_outcomes(vault_root=tmp_path)
    assert count == 0


def test_poll_outcomes_records_a_resolved_issue_as_accepted(tmp_path: Path) -> None:
    """Telemetry findings are filed as issues; their closure is the outcome signal.

    ``COMPLETED`` means a human acted on the finding — the same signal a merged
    PR carries. Without this the autopilot would keep re-filing findings and
    never learn whether they landed.
    """
    issues = [{"number": 12, "state": "CLOSED", "stateReason": "COMPLETED"}]
    with patch("subprocess.run", side_effect=[_make_gh_output([]), _make_gh_output(issues)]), \
         patch("mnemo.autopilot.selffix.outcome_poller.pr_budget.record_outcome") as mock_rec:
        count = poll_outcomes(vault_root=tmp_path)
    assert count == 1
    mock_rec.assert_called_once_with(
        vault_root=tmp_path, pr_number=12, outcome="merged"
    )


def test_poll_outcomes_records_a_wont_fix_issue_as_closed(tmp_path: Path) -> None:
    issues = [{"number": 13, "state": "CLOSED", "stateReason": "NOT_PLANNED"}]
    with patch("subprocess.run", side_effect=[_make_gh_output([]), _make_gh_output(issues)]), \
         patch("mnemo.autopilot.selffix.outcome_poller.pr_budget.record_outcome") as mock_rec:
        poll_outcomes(vault_root=tmp_path)
    mock_rec.assert_called_once_with(
        vault_root=tmp_path, pr_number=13, outcome="closed"
    )


def test_poll_outcomes_survives_gh_failing_on_issues(tmp_path: Path) -> None:
    prs = [{"number": 42, "state": "MERGED"}]
    proc_err = MagicMock()
    proc_err.returncode = 1
    proc_err.stdout = ""
    with patch("subprocess.run", side_effect=[_make_gh_output(prs), proc_err]), \
         patch("mnemo.autopilot.selffix.outcome_poller.pr_budget.record_outcome"):
        count = poll_outcomes(vault_root=tmp_path)
    assert count == 1


def test_poll_outcomes_skips_unknown_state(tmp_path: Path) -> None:
    prs = [{"number": 5, "state": "OPEN"}]
    with patch("subprocess.run", side_effect=[_make_gh_output(prs), _make_gh_output([])]), \
         patch("mnemo.autopilot.selffix.outcome_poller.pr_budget.record_outcome") as mock_rec:
        count = poll_outcomes(vault_root=tmp_path)
    assert count == 0
    mock_rec.assert_not_called()
