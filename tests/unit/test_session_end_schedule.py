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
