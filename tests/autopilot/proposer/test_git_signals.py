"""Tests for autopilot/proposer/_git_signals.py"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mnemo.autopilot.proposer._git_signals import (
    git_current_branch,
    git_diff_stat,
    git_log_since,
    git_modified_files,
    git_status_short,
)


def _make_result(stdout: str = "", returncode: int = 0) -> MagicMock:
    r = MagicMock()
    r.stdout = stdout
    r.returncode = returncode
    return r


def test_git_log_since_returns_messages(tmp_path: Path):
    with patch("subprocess.run", return_value=_make_result("fix typo\nadd validation\n")) as mock_run:
        msgs = git_log_since(tmp_path, "2026-01-01T00:00:00Z")
    assert msgs == ["fix typo", "add validation"]
    args = mock_run.call_args[0][0]
    assert "git" in args
    assert "--since=2026-01-01T00:00:00Z" in args


def test_git_log_since_empty_on_nonzero(tmp_path: Path):
    with patch("subprocess.run", return_value=_make_result("output", returncode=1)):
        msgs = git_log_since(tmp_path, "2026-01-01T00:00:00Z")
    assert msgs == []


def test_git_log_since_empty_on_file_not_found(tmp_path: Path):
    with patch("subprocess.run", side_effect=FileNotFoundError):
        msgs = git_log_since(tmp_path, "2026-01-01T00:00:00Z")
    assert msgs == []


def test_git_diff_stat_returns_stat(tmp_path: Path):
    stat = " 5 files changed, 20 insertions(+)"
    with patch("subprocess.run", return_value=_make_result(stat)):
        result = git_diff_stat(tmp_path, "abc123")
    assert "5 files changed" in result


def test_git_diff_stat_empty_on_error(tmp_path: Path):
    with patch("subprocess.run", return_value=_make_result("", returncode=128)):
        result = git_diff_stat(tmp_path, "abc123")
    assert result == ""


def test_git_current_branch_returns_branch(tmp_path: Path):
    with patch("subprocess.run", return_value=_make_result("main\n")):
        branch = git_current_branch(tmp_path)
    assert branch == "main"


def test_git_current_branch_empty_on_failure(tmp_path: Path):
    with patch("subprocess.run", side_effect=FileNotFoundError):
        branch = git_current_branch(tmp_path)
    assert branch == ""


def test_git_status_short_returns_output(tmp_path: Path):
    status = " M src/foo.py\n?? bar.py"
    with patch("subprocess.run", return_value=_make_result(status)):
        result = git_status_short(tmp_path)
    assert "src/foo.py" in result


def test_git_modified_files_parses_porcelain(tmp_path: Path):
    status = " M src/foo.py\n?? bar.py\n"
    with patch("subprocess.run", return_value=_make_result(status)):
        files = git_modified_files(tmp_path)
    assert "src/foo.py" in files
    assert "bar.py" in files


def test_git_modified_files_empty_when_no_git(tmp_path: Path):
    with patch("subprocess.run", side_effect=FileNotFoundError):
        files = git_modified_files(tmp_path)
    assert files == []
