"""Tests for mnemo.core.log_utils — shared log rotation."""
from __future__ import annotations

from pathlib import Path

from mnemo.core.log_utils import rotate_if_needed


def test_rotate_noop_below_threshold(tmp_path):
    log = tmp_path / "test.jsonl"
    log.write_text("small\n")
    rotate_if_needed(log, max_bytes=1000)
    assert log.exists()
    assert not log.with_suffix(".jsonl.1").exists()


def test_rotate_triggers_at_threshold(tmp_path):
    log = tmp_path / "test.jsonl"
    log.write_text("x" * 1001)
    rotate_if_needed(log, max_bytes=1000)
    assert not log.exists()
    assert log.with_suffix(".jsonl.1").exists()
    assert log.with_suffix(".jsonl.1").read_text() == "x" * 1001


def test_rotate_overwrites_existing_dot_one(tmp_path):
    log = tmp_path / "test.jsonl"
    rotated = log.with_suffix(".jsonl.1")
    rotated.write_text("old rotated content")
    log.write_text("x" * 2000)
    rotate_if_needed(log, max_bytes=1000)
    assert rotated.read_text() == "x" * 2000
    assert not log.exists()


def test_rotate_missing_file_noop(tmp_path):
    log = tmp_path / "nonexistent.jsonl"
    rotate_if_needed(log, max_bytes=1000)


def test_rotate_oserror_silent(tmp_path):
    log = tmp_path / "test.jsonl"
    log.write_text("x" * 2000)
    log.chmod(0o000)
    try:
        rotate_if_needed(log, max_bytes=1000)
    finally:
        # On Linux, rename succeeds on a 0o000 file (directory write perm is
        # sufficient). Restore permissions on whichever path still exists.
        for candidate in (log, log.with_suffix(".jsonl.1")):
            try:
                candidate.chmod(0o644)
            except (FileNotFoundError, PermissionError):
                pass
