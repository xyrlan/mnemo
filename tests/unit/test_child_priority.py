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


# --- lowering --------------------------------------------------------------

SID = "a7e6d951-f8f9-4fe5-a04c-58d0e38fa59e"


@pytest.fixture
def entry(windows, monkeypatch) -> Dict:
    """The registry names a live bg session; the setter records what it gets."""
    found = {"pid": 71084, "sessionId": SID, "kind": "bg", "procStart": "1343"}
    monkeypatch.setattr("mnemo.core.sessions.registry.live_entry",
                        lambda sid, directory=None: found if sid == SID else None)
    found["lowered"] = []
    monkeypatch.setattr(child_priority, "_set_below_normal",
                        lambda pid, start: found["lowered"].append((pid, start)))
    return found


def test_without_the_marker_nothing_is_looked_at(windows, monkeypatch) -> None:
    monkeypatch.setattr("mnemo.core.sessions.registry.live_entry",
                        lambda *a, **k: pytest.fail("the registry was read"))
    assert child_priority.lower_session(SID, None, environ={}) is None
    assert child_priority.lower_session(
        SID, None, environ={child_priority.ENV: "normal"}) is None


def test_the_registry_named_session_is_the_one_lowered(entry) -> None:
    assert child_priority.lower_session(SID, None, environ=MARKER) is None
    assert entry["lowered"] == [(71084, 1343)]


def test_an_interactive_session_is_left_alone_even_when_marked(entry) -> None:
    """An interactive `claude --resume` of a child could carry its settings."""
    entry["kind"] = "interactive"
    assert child_priority.lower_session(SID, None, environ=MARKER) is None
    assert entry["lowered"] == []


def test_normal_in_the_current_config_holds_for_a_child_marked_before(entry) -> None:
    """A wake restores the marker the child was dispatched with; the config wins."""
    cfg = {"dispatch": {"childPriority": "normal"}}
    assert child_priority.lower_session(SID, cfg, environ=MARKER) is None
    assert entry["lowered"] == []


def test_no_live_entry_is_a_reason(entry) -> None:
    assert "no live entry" in child_priority.lower_session("other", None, environ=MARKER)
    assert "no session id" in child_priority.lower_session("", None, environ=MARKER)
    assert entry["lowered"] == []


def test_a_failure_is_returned_not_raised(entry, monkeypatch) -> None:
    def denied(pid, start):
        raise PermissionError("access denied")

    monkeypatch.setattr(child_priority, "_set_below_normal", denied)
    assert "access denied" in child_priority.lower_session(SID, None, environ=MARKER)


def test_posix_does_nothing_even_when_marked(monkeypatch) -> None:
    monkeypatch.setattr(child_priority, "supported", lambda: False)
    monkeypatch.setattr("mnemo.core.sessions.registry.live_entry",
                        lambda *a, **k: pytest.fail("the registry was read"))
    assert child_priority.lower_session(SID, None, environ=MARKER) is None


@pytest.mark.parametrize("cfg", [{"dispatch": "x"}, {"dispatch": ["a"]}, "junk", None,
                                 {"dispatch": {"childPriority": 3}}])
def test_a_malformed_config_keeps_the_default_and_never_raises(cfg) -> None:
    assert child_priority.wanted(cfg)


@pytest.mark.real_spawn
def test_a_malformed_config_does_not_abort_the_dispatch(windows, run, tmp_path) -> None:
    config_path = Path(os.environ["MNEMO_CONFIG_PATH"])
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"dispatch": "x"}), encoding="utf-8")
    assert dispatch.spawn_child("P", cwd=tmp_path, lean=False) == "0000000b"
    assert _settings_files(run.args) == [{"env": MARKER}]


@pytest.mark.skipif(sys.platform != "win32", reason="priority classes are Windows")
def test_a_real_process_is_lowered_and_a_wrong_start_time_is_refused() -> None:
    from mnemo.core.sessions import registry

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        start = registry.windows_creation_time(proc.pid)
        assert start
        assert "no longer the session" in child_priority._set_below_normal(proc.pid, start + 1)
        assert _class_of(proc.pid) == 0x0020
        assert child_priority._set_below_normal(proc.pid, start) is None
        assert child_priority._set_below_normal(proc.pid, start) is None  # idempotent
        assert _class_of(proc.pid) == 0x4000
    finally:
        proc.kill()
        proc.wait()


def _class_of(pid: int) -> int:
    from mnemo.core.sessions import registry

    kernel32 = registry._kernel32()
    handle = kernel32.OpenProcess(0x1000, False, pid)
    assert handle
    try:
        return kernel32.GetPriorityClass(handle)
    finally:
        kernel32.CloseHandle(handle)

# --- the hook --------------------------------------------------------------

def _run_hook(monkeypatch, cwd: Path, vault: Path) -> str:
    payload = {"session_id": SID, "cwd": str(cwd), "source": "startup"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda _cfg=None: vault)
    monkeypatch.setattr("mnemo.core.hook_guard.throwaway_session", lambda *_a: False)
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    session_start.main()
    return out.getvalue()


def test_the_hook_lowers_with_its_session_id_before_it_does_its_work(
    monkeypatch, tmp_path: Path,
) -> None:
    order: List[str] = []
    monkeypatch.setenv(child_priority.ENV, child_priority.BELOW_NORMAL)
    monkeypatch.setattr(child_priority, "lower_session",
                        lambda sid, cfg: order.append(f"lower {sid}"))
    monkeypatch.setattr("mnemo.core.sync.roles.is_satellite",
                        lambda *a, **k: order.append("work") or False)
    cwd = tmp_path / "proj"
    cwd.mkdir()
    _run_hook(monkeypatch, cwd, tmp_path / "v")
    assert order[:2] == [f"lower {SID}", "work"]


def test_an_unmarked_session_never_reaches_the_lowering(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(child_priority, "lower_session",
                        lambda *a: pytest.fail("an interactive session was considered"))
    cwd = tmp_path / "proj"
    cwd.mkdir()
    _run_hook(monkeypatch, cwd, tmp_path / "v")


def test_a_failure_to_lower_is_logged_and_changes_no_output(
    monkeypatch, tmp_path: Path,
) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    monkeypatch.setenv(child_priority.ENV, child_priority.BELOW_NORMAL)
    monkeypatch.setattr(child_priority, "lower_session", lambda *a: None)
    clean = _run_hook(monkeypatch, cwd, tmp_path / "v1")

    monkeypatch.setattr(child_priority, "lower_session", lambda *a: "access denied")
    failed = _run_hook(monkeypatch, cwd, tmp_path / "v2")

    assert failed == clean.replace(str(tmp_path / "v1"), str(tmp_path / "v2"))
    rows = [json.loads(line) for line in
            (tmp_path / "v2" / errors.ERROR_LOG_NAME).read_text(encoding="utf-8").splitlines()]
    assert [r["message"] for r in rows if r["where"] == "child_priority.lower"] == ["access denied"]


def test_a_failure_is_logged_even_when_the_hook_then_returns_early(
    monkeypatch, tmp_path: Path,
) -> None:
    """The breaker being open is one of the hook's early returns."""
    monkeypatch.setenv(child_priority.ENV, child_priority.BELOW_NORMAL)
    monkeypatch.setattr(child_priority, "lower_session", lambda *a: "access denied")
    monkeypatch.setattr(errors, "should_run", lambda _v: False)
    cwd = tmp_path / "proj"
    cwd.mkdir()
    _run_hook(monkeypatch, cwd, tmp_path / "v")
    log = tmp_path / "v" / errors.ERROR_LOG_NAME
    assert "child_priority.lower" in log.read_text(encoding="utf-8")

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
