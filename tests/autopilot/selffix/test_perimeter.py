"""Tests for autopilot selffix perimeter guard."""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo.autopilot.selffix._perimeter import (
    ALLOWED_PATHS,
    PerimeterViolation,
    assert_perimeter,
    is_within_perimeter,
)


def test_allowed_path_shared(tmp_path: Path) -> None:
    repo = tmp_path
    diff = [repo / "shared" / "some-rule.md"]
    assert_perimeter(diff, repo_root=repo)  # must not raise


def test_allowed_path_mnemo(tmp_path: Path) -> None:
    repo = tmp_path
    diff = [repo / ".mnemo" / "autopilot.json"]
    assert_perimeter(diff, repo_root=repo)


def test_allowed_path_docs(tmp_path: Path) -> None:
    repo = tmp_path
    diff = [repo / "docs" / "some-doc.md"]
    assert_perimeter(diff, repo_root=repo)


def test_allowed_path_briefings(tmp_path: Path) -> None:
    repo = tmp_path
    diff = [repo / "briefings" / "session-briefing.md"]
    assert_perimeter(diff, repo_root=repo)


def test_allowed_path_archive(tmp_path: Path) -> None:
    repo = tmp_path
    diff = [repo / "src" / "mnemo" / "autopilot" / "_archive" / "old.md"]
    assert_perimeter(diff, repo_root=repo)


def test_forbidden_path_src(tmp_path: Path) -> None:
    repo = tmp_path
    diff = [repo / "src" / "mnemo" / "core.py"]
    with pytest.raises(PerimeterViolation, match="src/mnemo/core.py"):
        assert_perimeter(diff, repo_root=repo)


def test_forbidden_path_root_file(tmp_path: Path) -> None:
    repo = tmp_path
    diff = [repo / "pyproject.toml"]
    with pytest.raises(PerimeterViolation):
        assert_perimeter(diff, repo_root=repo)


def test_forbidden_path_tests(tmp_path: Path) -> None:
    repo = tmp_path
    diff = [repo / "tests" / "test_foo.py"]
    with pytest.raises(PerimeterViolation):
        assert_perimeter(diff, repo_root=repo)


def test_mixed_diff_raises_on_forbidden(tmp_path: Path) -> None:
    """Even one out-of-bound path in a mixed diff should raise."""
    repo = tmp_path
    diff = [
        repo / "shared" / "rule.md",       # allowed
        repo / "pyproject.toml",            # forbidden
    ]
    with pytest.raises(PerimeterViolation):
        assert_perimeter(diff, repo_root=repo)


def test_empty_diff_is_ok(tmp_path: Path) -> None:
    repo = tmp_path
    assert_perimeter([], repo_root=repo)


def test_is_within_perimeter_true(tmp_path: Path) -> None:
    repo = tmp_path
    assert is_within_perimeter(repo / "shared" / "rule.md", repo_root=repo) is True


def test_is_within_perimeter_false(tmp_path: Path) -> None:
    repo = tmp_path
    assert is_within_perimeter(repo / "pyproject.toml", repo_root=repo) is False


def test_allowed_paths_constant_has_expected_entries() -> None:
    assert "shared/" in ALLOWED_PATHS
    assert ".mnemo/" in ALLOWED_PATHS
    assert "docs/" in ALLOWED_PATHS
    assert "briefings/" in ALLOWED_PATHS
