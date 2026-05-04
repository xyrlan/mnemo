from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mnemo.autopilot.core.kill_switch import set_state
from mnemo.autopilot.core.triggers import (
    last_run,
    mark_run,
    run_detached,
    run_inline,
    runs_path,
    should_run,
)


def test_should_run_false_when_kill_switch_off(tmp_path: Path):
    set_state(vault_root=tmp_path, state="off")
    assert should_run(vault_root=tmp_path, name="x", interval_days=7) is False


def test_should_run_true_when_active_and_never_ran(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")
    assert should_run(vault_root=tmp_path, name="x", interval_days=7) is True


def test_should_run_false_when_recently_ran(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")
    mark_run(vault_root=tmp_path, name="x")
    assert should_run(vault_root=tmp_path, name="x", interval_days=7) is False


def test_should_run_true_when_old_run(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")
    mark_run(vault_root=tmp_path, name="x")
    # backdate by 8 days
    p = runs_path(tmp_path)
    data = json.loads(p.read_text())
    old = (datetime.now(timezone.utc) - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["runs"]["x"]["last_run_at"] = old
    p.write_text(json.dumps(data))
    assert should_run(vault_root=tmp_path, name="x", interval_days=7) is True


def test_mark_run_failure_does_not_update_last_run(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")
    mark_run(vault_root=tmp_path, name="x", success=False, error="boom")
    assert last_run(vault_root=tmp_path, name="x") is None
    data = json.loads(runs_path(tmp_path).read_text())
    assert data["runs"]["x"]["last_error"] == "boom"
    assert "last_attempt_at" in data["runs"]["x"]


def test_mark_run_success_clears_prior_error(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")
    mark_run(vault_root=tmp_path, name="x", success=False, error="boom")
    mark_run(vault_root=tmp_path, name="x", success=True)
    data = json.loads(runs_path(tmp_path).read_text())
    assert "last_error" not in data["runs"]["x"]
    assert data["runs"]["x"]["last_run_at"] is not None


def test_run_inline_marks_success(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")
    calls = []

    def fn():
        calls.append(1)

    ok = run_inline(vault_root=tmp_path, name="x", fn=fn)
    assert ok is True
    assert calls == [1]
    assert last_run(vault_root=tmp_path, name="x") is not None


def test_run_inline_swallows_exception(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")

    def fn():
        raise RuntimeError("boom")

    ok = run_inline(vault_root=tmp_path, name="x", fn=fn)
    assert ok is False
    assert last_run(vault_root=tmp_path, name="x") is None
    data = json.loads(runs_path(tmp_path).read_text())
    assert "boom" in data["runs"]["x"]["last_error"]


def test_run_detached_records_attempt(tmp_path: Path, monkeypatch):
    set_state(vault_root=tmp_path, state="on")
    spawned = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            spawned["argv"] = argv
            spawned["kwargs"] = kwargs

    monkeypatch.setattr("mnemo.autopilot.core.triggers.subprocess.Popen", FakePopen)

    run_detached(vault_root=tmp_path, name="bg", argv=["echo", "hi"])
    assert spawned["argv"] == ["echo", "hi"]
    # detached marks OPTIMISTIC success after spawn so the interval gate
    # prevents re-launching the same job every SessionStart while the
    # subprocess is still running. Failures land in autopilot-runs.log.
    assert last_run(vault_root=tmp_path, name="bg") is not None
    data = json.loads(runs_path(tmp_path).read_text())
    assert "last_error" not in data["runs"]["bg"]


def test_run_detached_swallows_oserror(tmp_path: Path, monkeypatch):
    set_state(vault_root=tmp_path, state="on")

    def boom(*a, **kw):
        raise OSError("nope")

    monkeypatch.setattr("mnemo.autopilot.core.triggers.subprocess.Popen", boom)
    # should not raise
    run_detached(vault_root=tmp_path, name="bg", argv=["x"])
    data = json.loads(runs_path(tmp_path).read_text())
    assert "nope" in data["runs"]["bg"]["last_error"]
