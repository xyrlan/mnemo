"""Tests for the thin gh CLI wrapper."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mnemo.autopilot.selffix._gh import (
    open_issue,
    open_pr,
    push_branch,
)


def _make_proc(returncode: int = 0, stdout: str = "42\n") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = ""
    return proc


# ---------------------------------------------------------------------------
# open_issue
# ---------------------------------------------------------------------------


def test_open_issue_parses_the_number_from_the_url(tmp_path: Path) -> None:
    url = "https://github.com/acme/mnemo/issues/128\n"
    with patch("subprocess.run", return_value=_make_proc(0, stdout=url)):
        result = open_issue(
            title="fix(autopilot): telemetry anomalies",
            body="report",
            labels=["mnemo:self-fix"],
            repo_root=tmp_path,
        )
    assert result == 128


def test_open_issue_passes_labels(tmp_path: Path) -> None:
    url = "https://github.com/acme/mnemo/issues/1\n"
    with patch("subprocess.run", return_value=_make_proc(0, stdout=url)) as mock_run:
        open_issue(
            title="t", body="b", labels=["mnemo:self-fix"], repo_root=tmp_path
        )
    cmd = mock_run.call_args[0][0]
    assert cmd[:3] == ["gh", "issue", "create"]
    assert "--label" in cmd and "mnemo:self-fix" in cmd


def test_open_issue_returns_none_when_gh_missing(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError):
        result = open_issue(title="t", body="b", labels=[], repo_root=tmp_path)
    assert result is None


def test_open_issue_returns_none_on_nonzero(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_make_proc(1, stdout="")):
        result = open_issue(title="t", body="b", labels=[], repo_root=tmp_path)
    assert result is None


def test_open_issue_returns_none_on_unparseable_output(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_make_proc(0, stdout="created ok\n")):
        result = open_issue(title="t", body="b", labels=[], repo_root=tmp_path)
    assert result is None


# ---------------------------------------------------------------------------
# push_branch
# ---------------------------------------------------------------------------


def test_push_branch_returns_true_on_success(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_make_proc(0)):
        result = push_branch("mnemo/self-fix/doctor-2026-04-30", repo_root=tmp_path)
    assert result is True


def test_push_branch_returns_false_when_gh_missing(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError):
        result = push_branch("mnemo/self-fix/doctor-2026-04-30", repo_root=tmp_path)
    assert result is False


# ---------------------------------------------------------------------------
# open_pr
# ---------------------------------------------------------------------------


def test_open_pr_returns_pr_number(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_make_proc(0, stdout="42\n")):
        result = open_pr(
            branch="mnemo/self-fix/doctor-2026-04-30",
            title="fix: doctor warnings",
            body="Fixed 3 warnings",
            labels=["mnemo:self-fix"],
            draft=True,
            repo_root=tmp_path,
        )
    assert result == 42


def test_open_pr_returns_none_when_gh_missing(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError):
        result = open_pr(
            branch="mnemo/self-fix/doctor-2026-04-30",
            title="fix: doctor warnings",
            body="Fixed 3 warnings",
            labels=["mnemo:self-fix"],
            draft=False,
            repo_root=tmp_path,
        )
    assert result is None


def test_open_pr_returns_none_on_nonzero(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_make_proc(1, stdout="")):
        result = open_pr(
            branch="mnemo/self-fix/doctor-2026-04-30",
            title="fix",
            body="body",
            labels=[],
            draft=False,
            repo_root=tmp_path,
        )
    assert result is None


def test_open_pr_includes_draft_flag(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_make_proc(0, stdout="7\n")) as mock_run:
        open_pr(
            branch="mnemo/self-fix/doctor-2026-04-30",
            title="fix",
            body="body",
            labels=[],
            draft=True,
            repo_root=tmp_path,
        )
    cmd = mock_run.call_args[0][0]
    assert "--draft" in cmd


def test_open_pr_no_draft_flag_when_false(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_make_proc(0, stdout="7\n")) as mock_run:
        open_pr(
            branch="mnemo/self-fix/doctor-2026-04-30",
            title="fix",
            body="body",
            labels=[],
            draft=False,
            repo_root=tmp_path,
        )
    cmd = mock_run.call_args[0][0]
    assert "--draft" not in cmd
