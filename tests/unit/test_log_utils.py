"""Tests for mnemo.core.log_utils — shared log rotation."""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core.log_utils import iter_rotated_rows, rotate_if_needed


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


# ---------------------------------------------------------------------------
# iter_rotated_rows
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [row if isinstance(row, str) else json.dumps(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_iter_rotated_rows_both_files_rotated_first_then_live(tmp_path):
    live = tmp_path / "log.jsonl"
    rotated = tmp_path / "log.jsonl.1"
    _write_jsonl(rotated, [{"n": 1}, {"n": 2}])
    _write_jsonl(live, [{"n": 3}, {"n": 4}])
    assert [r["n"] for r in iter_rotated_rows(live)] == [1, 2, 3, 4]


def test_iter_rotated_rows_only_live(tmp_path):
    live = tmp_path / "log.jsonl"
    _write_jsonl(live, [{"n": 1}, {"n": 2}])
    assert [r["n"] for r in iter_rotated_rows(live)] == [1, 2]


def test_iter_rotated_rows_only_rotated(tmp_path):
    live = tmp_path / "log.jsonl"
    rotated = tmp_path / "log.jsonl.1"
    _write_jsonl(rotated, [{"n": 1}, {"n": 2}])
    assert [r["n"] for r in iter_rotated_rows(live)] == [1, 2]


def test_iter_rotated_rows_neither_file_is_empty(tmp_path):
    live = tmp_path / "log.jsonl"
    assert list(iter_rotated_rows(live)) == []


def test_iter_rotated_rows_skips_a_torn_last_line(tmp_path):
    live = tmp_path / "log.jsonl"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text(json.dumps({"n": 1}) + "\n{not valid json", encoding="utf-8")
    assert [r["n"] for r in iter_rotated_rows(live)] == [1]


def test_iter_rotated_rows_skips_non_dict_rows(tmp_path):
    live = tmp_path / "log.jsonl"
    _write_jsonl(live, [{"n": 1}, "[1, 2, 3]", "42", '"a string"'])
    assert [r["n"] for r in iter_rotated_rows(live)] == [1]


def test_iter_rotated_rows_skips_blank_lines(tmp_path):
    live = tmp_path / "log.jsonl"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text(f"\n\n{json.dumps({'n': 1})}\n\n", encoding="utf-8")
    assert [r["n"] for r in iter_rotated_rows(live)] == [1]


def test_iter_rotated_rows_oserror_on_one_file_does_not_raise(tmp_path):
    live = tmp_path / "log.jsonl"
    rotated = tmp_path / "log.jsonl.1"
    _write_jsonl(rotated, [{"n": 1}])
    live.mkdir()  # a directory where the live file should be → OSError on open
    assert [r["n"] for r in iter_rotated_rows(live)] == [1]
