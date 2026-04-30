"""Tests for the thin gh CLI wrapper."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mnemo.autopilot.selffix._gh import (
    create_branch,
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
# create_branch
# ---------------------------------------------------------------------------


def test_create_branch_returns_branch_name(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_make_proc(0)) as mock_run:
        result = create_branch("mnemo/self-fix/doctor-2026-04-30", repo_root=tmp_path)
    assert result == "mnemo/self-fix/doctor-2026-04-30"
    assert mock_run.called


def test_create_branch_returns_none_when_gh_missing(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError):
        result = create_branch("mnemo/self-fix/doctor-2026-04-30", repo_root=tmp_path)
    assert result is None


def test_create_branch_returns_none_on_nonzero(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_make_proc(1)):
        result = create_branch("mnemo/self-fix/doctor-2026-04-30", repo_root=tmp_path)
    assert result is None


def test_create_branch_returns_none_on_oserror(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=OSError("no gh")):
        result = create_branch("mnemo/self-fix/doctor-2026-04-30", repo_root=tmp_path)
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
