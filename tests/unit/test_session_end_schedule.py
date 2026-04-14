"""v0.3 background-schedule unit tests for session_end.py helpers."""
from __future__ import annotations

import json
import os
import time


def _write_state(path, last_run):
    payload = {
        "schema_version": 2,
        "last_run": last_run,
        "entries": {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _touch_memory(vault, agent, name, mtime_offset=0):
    path = vault / "bots" / agent / "memory" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\nname: x\ntype: feedback\n---\nbody\n")
    if mtime_offset:
        atime = path.stat().st_atime
        mtime = path.stat().st_mtime + mtime_offset
        os.utime(path, (atime, mtime))


def test_debounce_passes_when_count_and_time_both_ok(tmp_path):
    from datetime import datetime

    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    state_path = vault / ".mnemo" / "extraction-state.json"
    _write_state(state_path, "2026-04-13T10:00:00")
    for i in range(5):
        _touch_memory(vault, "agent_a", f"feedback_{i}.md")

    cfg = {"extraction": {"auto": {"minNewMemories": 5, "minIntervalMinutes": 60}}}
    now = datetime.fromisoformat("2026-04-13T12:00:00")

    assert session_end._debounce_passes(state_path, vault, cfg, now=now) is True


def test_debounce_fails_when_count_below_threshold(tmp_path):
    from datetime import datetime

    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    state_path = vault / ".mnemo" / "extraction-state.json"
    _write_state(state_path, "2026-04-13T10:00:00")
    for i in range(3):
        _touch_memory(vault, "agent_a", f"feedback_{i}.md")

    cfg = {"extraction": {"auto": {"minNewMemories": 5, "minIntervalMinutes": 60}}}
    now = datetime.fromisoformat("2026-04-13T12:00:00")

    assert session_end._debounce_passes(state_path, vault, cfg, now=now) is False


def test_debounce_fails_when_time_below_floor(tmp_path):
    from datetime import datetime

    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    state_path = vault / ".mnemo" / "extraction-state.json"
    _write_state(state_path, "2026-04-13T11:30:00")
    for i in range(10):
        _touch_memory(vault, "agent_a", f"feedback_{i}.md")

    cfg = {"extraction": {"auto": {"minNewMemories": 5, "minIntervalMinutes": 60}}}
    now = datetime.fromisoformat("2026-04-13T12:00:00")  # only 30 min after

    assert session_end._debounce_passes(state_path, vault, cfg, now=now) is False


def test_debounce_passes_when_last_run_is_none(tmp_path):
    from datetime import datetime

    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    state_path = vault / ".mnemo" / "extraction-state.json"
    _write_state(state_path, None)
    for i in range(5):
        _touch_memory(vault, "agent_a", f"feedback_{i}.md")

    cfg = {"extraction": {"auto": {"minNewMemories": 5, "minIntervalMinutes": 60}}}
    now = datetime.fromisoformat("2026-04-13T12:00:00")

    # No prior run → elapsed is infinite → passes time gate
    assert session_end._debounce_passes(state_path, vault, cfg, now=now) is True


def test_lock_held_false_when_dir_absent(tmp_path):
    from mnemo.hooks import session_end

    lock = tmp_path / "extract.lock"
    assert session_end._lock_held(lock) is False


def test_lock_held_true_when_fresh_dir(tmp_path):
    from mnemo.hooks import session_end

    lock = tmp_path / "extract.lock"
    lock.mkdir()
    assert session_end._lock_held(lock) is True


def test_lock_held_false_when_stale_dir(tmp_path):
    from mnemo.hooks import session_end

    lock = tmp_path / "extract.lock"
    lock.mkdir()
    old = time.time() - 600
    os.utime(lock, (old, old))
    assert session_end._lock_held(lock) is False


def test_spawn_detached_extraction_posix_uses_start_new_session(monkeypatch):
    import subprocess
    import sys
    from mnemo.hooks import session_end

    captured = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs

    monkeypatch.setattr("subprocess.Popen", FakePopen)
    monkeypatch.setattr(sys, "platform", "linux")

    session_end._spawn_detached_extraction()

    assert captured["argv"][1:] == ["-m", "mnemo", "extract", "--background"]
    assert captured["kwargs"].get("start_new_session") is True
    assert captured["kwargs"].get("close_fds") is True
    assert captured["kwargs"]["stdin"] == subprocess.DEVNULL
    assert captured["kwargs"]["stdout"] == subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] == subprocess.DEVNULL


def test_spawn_detached_extraction_windows_uses_creationflags(monkeypatch):
    import sys
    from mnemo.hooks import session_end

    captured = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["kwargs"] = kwargs

    monkeypatch.setattr("subprocess.Popen", FakePopen)
    monkeypatch.setattr(sys, "platform", "win32")

    session_end._spawn_detached_extraction()

    flags = captured["kwargs"].get("creationflags", 0)
    # DETACHED_PROCESS = 0x00000008, CREATE_NEW_PROCESS_GROUP = 0x00000200
    assert flags & 0x00000008
    assert flags & 0x00000200
    assert "start_new_session" not in captured["kwargs"]


def test_schedule_extraction_no_op_when_auto_disabled(tmp_path, monkeypatch):
    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = {"extraction": {"auto": {"enabled": False}}}

    spawn_called = []
    monkeypatch.setattr(session_end, "_spawn_detached_extraction",
                        lambda: spawn_called.append(True))

    # v0.3.1: when auto is disabled the scheduler returns silently. The old
    # hint fallback (_maybe_emit_hint) was removed along with the write-only
    # hooks and the 🟡 daily-log notification path.
    session_end._maybe_schedule_extraction(cfg, vault, "agent_a")

    assert spawn_called == []


def test_schedule_extraction_spawns_when_debounce_passes(tmp_path, monkeypatch):
    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".mnemo").mkdir()

    cfg = {"extraction": {"auto": {
        "enabled": True,
        "minNewMemories": 1,
        "minIntervalMinutes": 0,
    }}}

    mem = vault / "bots" / "agent_a" / "memory" / "feedback_x.md"
    mem.parent.mkdir(parents=True)
    mem.write_text("---\ntype: feedback\n---\nbody\n")

    spawn_called = []
    monkeypatch.setattr(session_end, "_spawn_detached_extraction",
                        lambda: spawn_called.append(True))

    session_end._maybe_schedule_extraction(cfg, vault, "agent_a")

    assert spawn_called == [True]


def test_schedule_extraction_skips_when_lock_held(tmp_path, monkeypatch):
    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".mnemo" / "extract.lock").mkdir(parents=True)

    cfg = {"extraction": {"auto": {
        "enabled": True,
        "minNewMemories": 1,
        "minIntervalMinutes": 0,
    }}}

    spawn_called = []
    monkeypatch.setattr(session_end, "_spawn_detached_extraction",
                        lambda: spawn_called.append(True))

    mem = vault / "bots" / "agent_a" / "memory" / "feedback_x.md"
    mem.parent.mkdir(parents=True)
    mem.write_text("---\ntype: feedback\n---\nbody\n")

    session_end._maybe_schedule_extraction(cfg, vault, "agent_a")

    assert spawn_called == [], "should not spawn when lock is held"


def test_schedule_extraction_swallows_popen_errors(tmp_path, monkeypatch):
    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)

    cfg = {"extraction": {"auto": {
        "enabled": True,
        "minNewMemories": 1,
        "minIntervalMinutes": 0,
    }}}

    mem = vault / "bots" / "agent_a" / "memory" / "feedback_x.md"
    mem.parent.mkdir(parents=True)
    mem.write_text("---\ntype: feedback\n---\nbody\n")

    def boom():
        raise OSError("too many fds")
    monkeypatch.setattr(session_end, "_spawn_detached_extraction", boom)

    # Should not raise
    session_end._maybe_schedule_extraction(cfg, vault, "agent_a")

    errors_log = vault / ".errors.log"
    assert errors_log.exists()
    assert "session_end.schedule" in errors_log.read_text()
