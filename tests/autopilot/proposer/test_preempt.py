"""Tests for autopilot/proposer/preempt.py"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from mnemo.autopilot.proposer.preempt import (
    _cache_path,
    _cache_valid,
    predict_next_action,
    preload_mcp_cache,
    read_preempt_cache,
    write_preempt_cache,
)


def _fresh_cache(
    tmp_path: Path,
    slugs: list[str],
    project: str = "test-proj",
    branch: str = "main",
    ttl_minutes: int = 30,
    minutes_ago: int = 0,
) -> dict:
    predicted_at = (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {
        "predicted_at": predicted_at,
        "project": project,
        "slugs": slugs,
        "ttl_minutes": ttl_minutes,
        "branch": branch,
    }
    cache_dir = tmp_path / ".mnemo"
    cache_dir.mkdir(parents=True, exist_ok=True)
    _cache_path(tmp_path).write_text(json.dumps(data, indent=2))
    return data


# --- write_preempt_cache ---

def test_write_preempt_cache_creates_file(tmp_path: Path):
    with patch("mnemo.autopilot.proposer.preempt.git_current_branch", return_value="main"):
        write_preempt_cache(
            vault_root=tmp_path,
            project="my-project",
            slugs=["slug-a", "slug-b"],
            cwd=tmp_path,
        )
    path = _cache_path(tmp_path)
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["project"] == "my-project"
    assert "slug-a" in data["slugs"]
    assert data["branch"] == "main"
    assert data["ttl_minutes"] == 30


def test_write_preempt_cache_truncates_to_10(tmp_path: Path):
    with patch("mnemo.autopilot.proposer.preempt.git_current_branch", return_value=""):
        write_preempt_cache(
            vault_root=tmp_path,
            project="p",
            slugs=[f"slug-{i}" for i in range(20)],
        )
    data = json.loads(_cache_path(tmp_path).read_text())
    assert len(data["slugs"]) == 10


def test_write_preempt_cache_no_cwd_ok(tmp_path: Path):
    write_preempt_cache(vault_root=tmp_path, project="p", slugs=["a"])
    assert _cache_path(tmp_path).exists()


# --- _cache_valid ---

def test_cache_valid_fresh(tmp_path: Path):
    data = _fresh_cache(tmp_path, ["a"], minutes_ago=1)
    with patch("mnemo.autopilot.proposer.preempt.git_current_branch", return_value="main"):
        assert _cache_valid(data, tmp_path) is True


def test_cache_invalid_expired(tmp_path: Path):
    data = _fresh_cache(tmp_path, ["a"], minutes_ago=35)
    with patch("mnemo.autopilot.proposer.preempt.git_current_branch", return_value="main"):
        assert _cache_valid(data, tmp_path) is False


def test_cache_invalid_branch_changed(tmp_path: Path):
    data = _fresh_cache(tmp_path, ["a"], branch="feature/old", minutes_ago=1)
    with patch("mnemo.autopilot.proposer.preempt.git_current_branch", return_value="main"):
        assert _cache_valid(data, tmp_path) is False


def test_cache_valid_no_cached_branch(tmp_path: Path):
    """When branch is '' in cache, branch check is skipped."""
    data = _fresh_cache(tmp_path, ["a"], branch="", minutes_ago=1)
    with patch("mnemo.autopilot.proposer.preempt.git_current_branch", return_value="main"):
        assert _cache_valid(data, tmp_path) is True


def test_cache_valid_malformed_timestamp(tmp_path: Path):
    data = {"predicted_at": "not-a-date", "slugs": [], "ttl_minutes": 30, "branch": ""}
    assert _cache_valid(data, tmp_path) is False


# --- read_preempt_cache ---

def test_read_preempt_cache_returns_data_when_valid(tmp_path: Path):
    _fresh_cache(tmp_path, ["slug-x"], minutes_ago=5)
    with patch("mnemo.autopilot.proposer.preempt.git_current_branch", return_value="main"):
        result = read_preempt_cache(vault_root=tmp_path, cwd=tmp_path)
    assert result is not None
    assert "slug-x" in result["slugs"]


def test_read_preempt_cache_none_when_missing(tmp_path: Path):
    result = read_preempt_cache(vault_root=tmp_path)
    assert result is None


def test_read_preempt_cache_none_when_stale(tmp_path: Path):
    _fresh_cache(tmp_path, ["slug-x"], minutes_ago=40)
    with patch("mnemo.autopilot.proposer.preempt.git_current_branch", return_value="main"):
        result = read_preempt_cache(vault_root=tmp_path, cwd=tmp_path)
    assert result is None


def test_read_preempt_cache_none_on_corrupt_json(tmp_path: Path):
    (tmp_path / ".mnemo").mkdir()
    _cache_path(tmp_path).write_text("{invalid json}")
    result = read_preempt_cache(vault_root=tmp_path)
    assert result is None


# --- preload_mcp_cache ---

def test_preload_mcp_cache_is_noop(tmp_path: Path):
    # Should not raise
    preload_mcp_cache(vault_root=tmp_path, slugs=["a", "b"])


# --- predict_next_action ---

def test_predict_next_action_returns_list(tmp_path: Path):
    with patch("mnemo.autopilot.proposer.preempt.git_current_branch", return_value="feat/something"), \
         patch("mnemo.autopilot.proposer.preempt.git_modified_files", return_value=[]), \
         patch("mnemo.autopilot.proposer.preempt._slugs_from_rule_index", return_value=[]):
        result = predict_next_action(vault_root=tmp_path, project="p", cwd=tmp_path)
    assert isinstance(result, list)


def test_predict_next_action_deduplicates(tmp_path: Path):
    with patch("mnemo.autopilot.proposer.preempt.git_current_branch", return_value="main"), \
         patch("mnemo.autopilot.proposer.preempt.git_modified_files", return_value=[]), \
         patch("mnemo.autopilot.proposer.preempt._slugs_from_rule_index",
               return_value=["slug-a", "slug-b"]):
        result = predict_next_action(vault_root=tmp_path, project="p", cwd=tmp_path)
    # No duplicates
    assert len(result) == len(set(result))


def test_predict_next_action_limited_to_10(tmp_path: Path):
    many_slugs = [f"slug-{i}" for i in range(20)]
    with patch("mnemo.autopilot.proposer.preempt.git_current_branch", return_value="main"), \
         patch("mnemo.autopilot.proposer.preempt.git_modified_files", return_value=[]), \
         patch("mnemo.autopilot.proposer.preempt._slugs_from_rule_index",
               return_value=many_slugs):
        result = predict_next_action(vault_root=tmp_path, project="p", cwd=tmp_path)
    assert len(result) <= 10


def test_predict_next_action_graceful_on_exception(tmp_path: Path):
    with patch("mnemo.autopilot.proposer.preempt.git_current_branch",
               side_effect=RuntimeError("fail")):
        # Should not raise
        result = predict_next_action(vault_root=tmp_path, project="p", cwd=tmp_path)
    assert isinstance(result, list)
