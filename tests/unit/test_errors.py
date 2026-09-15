from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from mnemo.core import errors


def test_log_error_writes_jsonl(tmp_vault: Path):
    try:
        raise ValueError("boom")
    except ValueError as e:
        errors.log_error(tmp_vault, "session_start", e)
    log = (tmp_vault / ".errors.log").read_text(encoding="utf-8").strip().splitlines()
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


def _write_log(vault: Path, rows: list[dict]) -> None:
    with open(vault / ".errors.log", "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _minutes_ago(minutes: int) -> str:
    return (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def test_should_run_false_at_threshold(tmp_vault: Path):
    _write_log(tmp_vault, [
        {"timestamp": _minutes_ago(m), "where": "test", "kind": "ValueError", "message": f"err{m}"}
        for m in range(11)
    ])
    assert errors.should_run(tmp_vault) is False


def test_one_sweeps_burst_is_one_strike(tmp_vault: Path):
    """#314, the real log: one ``mnemo sessions --consume-unblocks`` pass wrote
    27 ``unblocks.consume`` rows in the same second and paused every hook."""
    now = datetime.now().isoformat(timespec="seconds")
    _write_log(tmp_vault, [
        {"timestamp": now, "where": "unblocks.consume", "kind": "RuntimeError",
         "message": f"sid-{i}: no transcript with session id sid-{i} for this directory"}
        for i in range(27)
    ])
    assert errors.recent_strikes(tmp_vault) == 1
    assert errors.should_run(tmp_vault) is True
    assert errors.recent_summary(tmp_vault) == (27, [("unblocks.consume", 27)])


def test_a_failure_that_persists_across_minutes_still_trips(tmp_vault: Path):
    """The breaker's own job: a hook failing on every call keeps striking."""
    _write_log(tmp_vault, [
        {"timestamp": _minutes_ago(m), "where": "pre_tool_use.x", "kind": "OSError", "message": "m"}
        for m in range(11) for _ in range(5)
    ])
    assert errors.recent_strikes(tmp_vault) == 11
    assert errors.should_run(tmp_vault) is False


def test_distinct_failures_in_one_minute_still_trip(tmp_vault: Path):
    now = datetime.now().isoformat(timespec="seconds")
    _write_log(tmp_vault, [
        {"timestamp": now, "where": f"session_start.site{i}", "kind": "OSError", "message": "m"}
        for i in range(11)
    ])
    assert errors.should_run(tmp_vault) is False


def test_same_where_different_kind_is_a_separate_strike(tmp_vault: Path):
    now = datetime.now().isoformat(timespec="seconds")
    _write_log(tmp_vault, [
        {"timestamp": now, "where": "session_end.outer", "kind": f"Error{i}", "message": "m"}
        for i in range(11)
    ])
    assert errors.should_run(tmp_vault) is False


def test_should_run_ignores_old_errors(tmp_vault: Path):
    log_path = tmp_vault / ".errors.log"
    old = (datetime.now() - timedelta(hours=2)).isoformat()
    lines = [json.dumps({"timestamp": old, "where": "x", "kind": "E", "message": "m"}) for _ in range(20)]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
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


def test_should_run_ignores_extract_errors(tmp_vault: Path):
    # 11 extract.* errors should NOT trip the hook breaker
    for i in range(11):
        try:
            raise RuntimeError(f"extract err {i}")
        except RuntimeError as e:
            errors.log_error(tmp_vault, "extract.chunk", e)
    assert errors.should_run(tmp_vault) is True


def test_circuit_breaker_excludes_session_end_schedule(tmp_vault):
    from mnemo.core import errors as err_mod

    for _ in range(15):
        try:
            raise RuntimeError("boom")
        except RuntimeError as e:
            err_mod.log_error(tmp_vault, "session_end.schedule", e)

    assert err_mod.should_run(tmp_vault) is True, \
        "session_end.schedule errors must not trip the circuit breaker"


def test_circuit_breaker_still_trips_on_hook_errors(tmp_vault):
    from mnemo.core import errors as err_mod

    for i in range(15):
        try:
            raise RuntimeError("boom")
        except RuntimeError as e:
            err_mod.log_error(tmp_vault, f"session_end.outer{i}", e)

    assert err_mod.should_run(tmp_vault) is False, \
        "genuine hook errors should still trip the breaker"
