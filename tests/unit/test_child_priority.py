"""A dispatched child lowers its own session below normal CPU (#723)."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import pytest

from mnemo.core import child_priority, dispatch, errors
from mnemo.hooks import session_start

MARKER = {child_priority.ENV: child_priority.BELOW_NORMAL}

#: The ancestry measured on 2026-10-17: the hook (python) under two bashes
#: under the session, under the pty host, under the daemon, under WMI.
MEASURED: child_priority.Table = {
    100: (101, "python.exe"),
    101: (102, "bash.exe"),
    102: (103, "bash.exe"),
    103: (104, "claude.exe"),   # --session-id <id>: the session
    104: (105, "claude.exe"),   # --bg-pty-host
    105: (106, "claude.exe"),   # daemon run
    106: (4, "WmiPrvSE.exe"),
}


# --- the marker ------------------------------------------------------------

@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(child_priority, "supported", lambda: True)


def test_the_marker_is_added_to_no_settings(windows) -> None:
    assert child_priority.with_marker(None, None) == {"env": MARKER}


def test_the_marker_keeps_a_twins_keys_and_an_env_of_its_own(windows) -> None:
    given = {"autoMemoryEnabled": False, "env": {"FOO": "1"}}
    out = child_priority.with_marker(given, {})
    assert out == {"autoMemoryEnabled": False, "env": {"FOO": "1", **MARKER}}
    assert given == {"autoMemoryEnabled": False, "env": {"FOO": "1"}}


@pytest.mark.parametrize("value", ["normal", "Normal", " normal "])
def test_normal_in_the_config_writes_no_marker(windows, value) -> None:
    cfg = {"dispatch": {"childPriority": value}}
    assert child_priority.with_marker(None, cfg) is None
    assert child_priority.with_marker({"a": 1}, cfg) == {"a": 1}


@pytest.mark.parametrize("value", ["below_normal", "idle", "bogus", 3, None])
def test_anything_but_normal_keeps_the_default(windows, value) -> None:
    cfg = {"dispatch": {"childPriority": value}}
    assert child_priority.with_marker(None, cfg) == {"env": MARKER}


def test_the_default_config_lowers() -> None:
    from mnemo.core.config import DEFAULTS

    assert DEFAULTS["dispatch"]["childPriority"] == "below_normal"
    assert child_priority.wanted(DEFAULTS)


def test_no_marker_where_it_was_never_measured(monkeypatch) -> None:
    monkeypatch.setattr(child_priority, "supported", lambda: False)
    assert child_priority.with_marker(None, None) is None
    assert child_priority.with_marker({"a": 1}, None) == {"a": 1}


class _Run:
    def __init__(self) -> None:
        self.args: List[str] = []

    def __call__(self, args, **kwargs):
        self.args = list(args)
        return subprocess.CompletedProcess(args, 0, "backgrounded · 0000000b\n", "")


def _settings_files(args: List[str]) -> List[Dict]:
    return [json.loads(Path(args[i + 1]).read_text(encoding="utf-8"))
            for i, a in enumerate(args) if a == "--settings"]


@pytest.fixture
def run(monkeypatch) -> _Run:
    from mnemo.core import child_profile

    monkeypatch.setattr(child_profile, "mnemo_hooks", lambda: {"Stop": []})
    monkeypatch.setattr(child_profile, "mnemo_mcp_servers", lambda: {})
    monkeypatch.delenv("MNEMO_DISPATCH_FULL_PROFILE", raising=False)
    recorder = _Run()
    monkeypatch.setattr(dispatch.subprocess, "run", recorder)
    return recorder


@pytest.mark.real_spawn
def test_both_profiles_carry_the_marker_in_their_one_settings_file(
    windows, run, tmp_path: Path,
) -> None:
    dispatch.spawn_child("P", cwd=tmp_path)
    assert _settings_files(run.args) == [{"hooks": {"Stop": []}, "env": MARKER}]

    dispatch.spawn_child("P", cwd=tmp_path, lean=False, settings={"autoMemoryEnabled": False})
    assert _settings_files(run.args) == [{"autoMemoryEnabled": False, "env": MARKER}]
    assert run.args[-1] == "P"


@pytest.mark.real_spawn
def test_the_config_opt_out_reaches_the_spawn(windows, run, tmp_path: Path) -> None:
    config_path = Path(os.environ["MNEMO_CONFIG_PATH"])
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"dispatch": {"childPriority": "normal"}}),
                           encoding="utf-8")

    dispatch.spawn_child("P", cwd=tmp_path, lean=False)
    assert run.args == ["claude", "--bg", "P"]
    dispatch.spawn_child("P", cwd=tmp_path)
    assert _settings_files(run.args) == [{"hooks": {"Stop": []}}]


# --- finding the session ---------------------------------------------------

def test_the_first_claude_above_the_hook_is_the_session() -> None:
    assert child_priority.find_session(MEASURED, 100) == 103


def test_the_name_match_ignores_case() -> None:
    table = {**MEASURED, 103: (104, "Claude.EXE")}
    assert child_priority.find_session(table, 100) == 103


def test_the_hook_itself_is_never_the_answer() -> None:
    table = {1: (2, "claude.exe"), 2: (3, "explorer.exe")}
    assert child_priority.find_session(table, 1) is None


def test_no_claude_above_is_none() -> None:
    table = {1: (2, "python.exe"), 2: (3, "bash.exe"), 3: (0, "explorer.exe")}
    assert child_priority.find_session(table, 1) is None


def test_a_parent_that_exited_ends_the_walk() -> None:
    assert child_priority.find_session({1: (2, "python.exe")}, 1) is None


def test_a_reused_pid_cycle_ends_the_walk() -> None:
    table = {1: (2, "python.exe"), 2: (3, "bash.exe"), 3: (1, "bash.exe")}
    assert child_priority.find_session(table, 1) is None


def test_the_walk_is_bounded() -> None:
    table = {i: (i + 1, "bash.exe") for i in range(20)}
    table[20] = (0, "claude.exe")
    assert child_priority.find_session(table, 0) is None
    assert child_priority.find_session(table, 0, depth=20) == 20


# --- lowering --------------------------------------------------------------

def test_without_the_marker_nothing_is_looked_at(windows, monkeypatch) -> None:
    monkeypatch.setattr(child_priority, "_process_table",
                        lambda: pytest.fail("the process table was read"))
    assert child_priority.lower_session({}) is None
    assert child_priority.lower_session({child_priority.ENV: "normal"}) is None


def test_the_marked_session_is_the_one_lowered(windows, monkeypatch) -> None:
    lowered: List[int] = []
    monkeypatch.setattr(child_priority, "_process_table", lambda: MEASURED)
    monkeypatch.setattr(child_priority.os, "getpid", lambda: 100)
    monkeypatch.setattr(child_priority, "_set_below_normal",
                        lambda pid: lowered.append(pid))
    assert child_priority.lower_session(MARKER) is None
    assert lowered == [103]


def test_a_failure_is_returned_not_raised(windows, monkeypatch) -> None:
    def denied():
        raise PermissionError("access denied")

    monkeypatch.setattr(child_priority, "_process_table", denied)
    assert "access denied" in child_priority.lower_session(MARKER)


def test_no_session_found_is_a_reason(windows, monkeypatch) -> None:
    monkeypatch.setattr(child_priority, "_process_table", lambda: {})
    assert "claude.exe" in child_priority.lower_session(MARKER)


def test_posix_does_nothing_even_when_marked(monkeypatch) -> None:
    monkeypatch.setattr(child_priority, "supported", lambda: False)
    monkeypatch.setattr(child_priority, "_process_table",
                        lambda: pytest.fail("the process table was read"))
    assert child_priority.lower_session(MARKER) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Toolhelp is Windows")
def test_the_real_snapshot_knows_this_process_and_its_parent() -> None:
    table = child_priority._process_table()
    assert table[os.getpid()][0] == os.getppid()
    assert table[os.getpid()][1].lower().endswith(".exe")


@pytest.mark.skipif(sys.platform != "win32", reason="priority classes are Windows")
def test_a_real_process_is_lowered_and_stays_lowered() -> None:
    import ctypes

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert child_priority._set_below_normal(proc.pid) is None
        assert child_priority._set_below_normal(proc.pid) is None  # idempotent
        kernel32 = child_priority._kernel32()
        handle = kernel32.OpenProcess(0x1000, False, proc.pid)
        assert handle, ctypes.get_last_error()
        try:
            assert kernel32.GetPriorityClass(handle) == 0x4000
        finally:
            kernel32.CloseHandle(handle)
    finally:
        proc.kill()
        proc.wait()


# --- the hook --------------------------------------------------------------

def _run_hook(monkeypatch, cwd: Path, vault: Path) -> str:
    payload = {"session_id": "s1", "cwd": str(cwd), "source": "startup"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda _cfg=None: vault)
    monkeypatch.setattr("mnemo.core.hook_guard.throwaway_session", lambda *_a: False)
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    session_start.main()
    return out.getvalue()


def test_the_hook_lowers_before_it_reads_anything(monkeypatch, tmp_path: Path) -> None:
    order: List[str] = []
    monkeypatch.setattr(child_priority, "lower_session",
                        lambda *a: order.append("lower"))
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    monkeypatch.setattr("mnemo.core.config.load_config",
                        lambda *a, **k: order.append("config") or {})
    session_start.main()
    assert order[:1] == ["lower"]


def test_a_failure_to_lower_is_logged_and_changes_no_output(
    monkeypatch, tmp_path: Path,
) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    monkeypatch.setattr(child_priority, "lower_session", lambda *a: None)
    clean = _run_hook(monkeypatch, cwd, tmp_path / "v1")

    monkeypatch.setattr(child_priority, "lower_session", lambda *a: "access denied")
    failed = _run_hook(monkeypatch, cwd, tmp_path / "v2")

    assert failed == clean.replace(str(tmp_path / "v1"), str(tmp_path / "v2"))
    rows = [json.loads(line) for line in
            (tmp_path / "v2" / errors.ERROR_LOG_NAME).read_text(encoding="utf-8").splitlines()]
    assert [r["message"] for r in rows if r["where"] == "child_priority.lower"] == ["access denied"]


def test_a_child_that_cannot_be_lowered_never_trips_the_breaker(tmp_path: Path) -> None:
    for _ in range(errors.THRESHOLD_PER_HOUR + 5):
        errors.log_error(tmp_path, "child_priority.lower", OSError("access denied"))
    # Strikes are per minute; spread these over as many to be sure.
    log = tmp_path / errors.ERROR_LOG_NAME
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    now = datetime.now()
    for i, row in enumerate(rows):
        row["timestamp"] = now.replace(minute=i % 60, second=0).isoformat(timespec="seconds")
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert errors.should_run(tmp_path)
