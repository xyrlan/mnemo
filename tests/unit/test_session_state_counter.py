"""Tests for the MCP call counter (per-day rolling)."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from mnemo.core.mcp import session_state as counter


def test_read_today_returns_zero_when_file_missing(tmp_vault: Path):
    assert counter.read_today(tmp_vault) == 0


def test_increment_creates_file_and_starts_at_one(tmp_vault: Path):
    counter.increment(tmp_vault)
    assert counter.read_today(tmp_vault) == 1
    path = tmp_vault / ".mnemo" / "mcp-call-counter.json"
    data = json.loads(path.read_text())
    assert data["date"] == date.today().isoformat()
    assert data["count"] == 1


def test_increment_accumulates(tmp_vault: Path):
    for _ in range(5):
        counter.increment(tmp_vault)
    assert counter.read_today(tmp_vault) == 5


def test_increment_resets_when_date_rolls_over(tmp_vault: Path):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    path = tmp_vault / ".mnemo" / "mcp-call-counter.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": yesterday, "count": 99}))

    counter.increment(tmp_vault)

    assert counter.read_today(tmp_vault) == 1
    data = json.loads(path.read_text())
    assert data["date"] == date.today().isoformat()


def test_read_today_returns_zero_for_yesterday_file(tmp_vault: Path):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    path = tmp_vault / ".mnemo" / "mcp-call-counter.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": yesterday, "count": 99}))
    assert counter.read_today(tmp_vault) == 0


def test_read_today_handles_corrupt_json(tmp_vault: Path):
    path = tmp_vault / ".mnemo" / "mcp-call-counter.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json")
    assert counter.read_today(tmp_vault) == 0


def test_increment_recovers_from_corrupt_file(tmp_vault: Path):
    path = tmp_vault / ".mnemo" / "mcp-call-counter.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("garbage")

    counter.increment(tmp_vault)

    assert counter.read_today(tmp_vault) == 1


def test_read_today_handles_non_dict_root(tmp_vault: Path):
    path = tmp_vault / ".mnemo" / "mcp-call-counter.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]")
    assert counter.read_today(tmp_vault) == 0


def test_read_today_handles_non_int_count(tmp_vault: Path):
    today = date.today().isoformat()
    path = tmp_vault / ".mnemo" / "mcp-call-counter.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": today, "count": "not-a-number"}))
    assert counter.read_today(tmp_vault) == 0


def test_increment_writes_atomically_no_tmp_left(tmp_vault: Path):
    counter.increment(tmp_vault)
    counter.increment(tmp_vault)
    counter.increment(tmp_vault)
    files = list((tmp_vault / ".mnemo").iterdir())
    # Only the final file, no leftover .tmp
    assert [f.name for f in files] == ["mcp-call-counter.json"]
