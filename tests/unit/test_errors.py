from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from mnemo.core import errors


def test_log_error_writes_jsonl(tmp_vault: Path):
    try:
        raise ValueError("boom")
    except ValueError as e:
        errors.log_error(tmp_vault, "session_start", e)
    log = (tmp_vault / ".errors.log").read_text().strip().splitlines()
    assert len(log) == 1
    entry = json.loads(log[0])
    assert entry["where"] == "session_start"
    assert entry["kind"] == "ValueError"
    assert "boom" in entry["message"]
    assert "timestamp" in entry


def test_log_error_never_raises_when_vault_unwritable(tmp_path: Path):
    # Pointing into a non-existent parent should NOT raise
    bogus = tmp_path / "no" / "such" / "vault"
    try:
        raise RuntimeError("x")
    except RuntimeError as e:
        errors.log_error(bogus, "anywhere", e)  # silent


def test_should_run_true_when_no_log(tmp_vault: Path):
    assert errors.should_run(tmp_vault) is True


def test_should_run_true_under_threshold(tmp_vault: Path):
    for i in range(5):
        try:
            raise ValueError(f"err{i}")
        except ValueError as e:
            errors.log_error(tmp_vault, "test", e)
    assert errors.should_run(tmp_vault) is True


def test_should_run_false_at_threshold(tmp_vault: Path):
    for i in range(11):
        try:
            raise ValueError(f"err{i}")
        except ValueError as e:
            errors.log_error(tmp_vault, "test", e)
    assert errors.should_run(tmp_vault) is False


def test_should_run_ignores_old_errors(tmp_vault: Path):
    log_path = tmp_vault / ".errors.log"
    old = (datetime.now() - timedelta(hours=2)).isoformat()
    lines = [json.dumps({"timestamp": old, "where": "x", "kind": "E", "message": "m"}) for _ in range(20)]
    log_path.write_text("\n".join(lines) + "\n")
    assert errors.should_run(tmp_vault) is True


def test_reset_archives_log(tmp_vault: Path):
    try:
        raise ValueError("x")
    except ValueError as e:
        errors.log_error(tmp_vault, "test", e)
    errors.reset(tmp_vault)
    assert not (tmp_vault / ".errors.log").exists()
    archives = list(tmp_vault.glob(".errors.log.*"))
    assert len(archives) == 1


def test_log_rotation_at_5mb(tmp_vault: Path):
    log_path = tmp_vault / ".errors.log"
    log_path.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
    try:
        raise ValueError("trigger")
    except ValueError as e:
        errors.log_error(tmp_vault, "rotation", e)
    # After rotation, .errors.log only contains the new entry (small)
    assert log_path.stat().st_size < 1024
    assert any(p.name.startswith(".errors.log.") for p in tmp_vault.iterdir())
