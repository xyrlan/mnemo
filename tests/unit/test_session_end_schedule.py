"""v0.3 background-schedule unit tests for session_end.py helpers."""
from __future__ import annotations
import pytest

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
    path.write_text(json.dumps(payload), encoding="utf-8")


def _touch_memory(vault, agent, name, mtime_offset=0):
    path = vault / "bots" / agent / "memory" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\nname: x\ntype: feedback\n---\nbody\n", encoding="utf-8")
    if mtime_offset:
        atime = path.stat().st_atime
        mtime = path.stat().st_mtime + mtime_offset
        os.utime(path, (atime, mtime))


def _set_mtime(path, iso):
    """Pin a file's mtime to a wall-clock instant the debounce can compare."""
    from datetime import datetime

    ts = datetime.fromisoformat(iso).timestamp()
    os.utime(path, (ts, ts))


def _touch_briefing(vault, agent, name, mtime_offset=0):
    path = vault / "bots" / agent / "briefings" / "sessions" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntype: briefing\n---\nbody\n", encoding="utf-8")
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


def test_debounce_passes_on_a_never_extracted_vault(tmp_path):
    """The first extraction is never debounced.

    A fresh vault has zero memory files, so the count gate would hold it back
    forever — and that first pass is the one that shows the user mnemo works.
    """
    from datetime import datetime

    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    state_path = vault / ".mnemo" / "extraction-state.json"
    _write_state(state_path, None)
    _touch_briefing(vault, "agent_a", "sess-1.md")

    cfg = {"extraction": {"auto": {"minNewMemories": 5, "minIntervalMinutes": 60}}}
    now = datetime.fromisoformat("2026-04-13T12:00:00")

    assert session_end._debounce_passes(state_path, vault, cfg, now=now) is True


def test_debounce_fails_when_a_new_briefing_arrives_inside_the_interval(tmp_path):
    """New material does not buy a pass through the time gate."""
    from datetime import datetime

    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    state_path = vault / ".mnemo" / "extraction-state.json"
    _write_state(state_path, "2026-04-13T11:50:00")
    _touch_briefing(vault, "agent_a", "sess-1.md")
    _set_mtime(vault / "bots" / "agent_a" / "briefings" / "sessions" / "sess-1.md",
               "2026-04-13T11:55:00")

    cfg = {"extraction": {"auto": {"minNewMemories": 1, "minIntervalMinutes": 60}}}
    now = datetime.fromisoformat("2026-04-13T12:00:00")  # 10 min after

    assert session_end._debounce_passes(state_path, vault, cfg, now=now) is False


def test_debounce_passes_when_only_a_briefing_is_new(tmp_path):
    """A session that wrote no memory file still has a briefing to consolidate."""
    from datetime import datetime

    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    state_path = vault / ".mnemo" / "extraction-state.json"
    _write_state(state_path, "2026-04-13T10:00:00")
    _touch_briefing(vault, "agent_a", "sess-1.md")
    _set_mtime(vault / "bots" / "agent_a" / "briefings" / "sessions" / "sess-1.md",
               "2026-04-13T11:00:00")

    cfg = {"extraction": {"auto": {"minNewMemories": 1, "minIntervalMinutes": 60}}}
    now = datetime.fromisoformat("2026-04-13T12:00:00")  # 2 h after

    assert session_end._debounce_passes(state_path, vault, cfg, now=now) is True


def test_debounce_fails_when_nothing_is_new(tmp_path):
    """Time alone is not a reason to spend an LLM call."""
    from datetime import datetime

    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    state_path = vault / ".mnemo" / "extraction-state.json"
    _write_state(state_path, "2026-04-13T10:00:00")
    # Both files predate last_run, so neither counts as new material.
    _touch_briefing(vault, "agent_a", "sess-old.md")
    _touch_memory(vault, "agent_a", "feedback_old.md")
    for p in (
        vault / "bots" / "agent_a" / "briefings" / "sessions" / "sess-old.md",
        vault / "bots" / "agent_a" / "memory" / "feedback_old.md",
    ):
        _set_mtime(p, "2026-04-13T09:00:00")

    cfg = {"extraction": {"auto": {"minNewMemories": 1, "minIntervalMinutes": 60}}}
    now = datetime.fromisoformat("2026-04-13T12:00:00")  # 2 h after

    assert session_end._debounce_passes(state_path, vault, cfg, now=now) is False


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


@pytest.mark.real_spawn
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


@pytest.mark.real_spawn
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
    # CREATE_NO_WINDOW = 0x08000000, CREATE_NEW_PROCESS_GROUP = 0x00000200;
    # never DETACHED_PROCESS = 0x00000008 (#452)
    assert flags == 0x08000000 | 0x00000200
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
    mem.write_text("---\ntype: feedback\n---\nbody\n", encoding="utf-8")

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
    mem.write_text("---\ntype: feedback\n---\nbody\n", encoding="utf-8")

    session_end._maybe_schedule_extraction(cfg, vault, "agent_a")

    assert spawn_called == [], "should not spawn when lock is held"


# --- v0.3.1: per-session briefing scheduling --------------------------------


def test_resolve_session_jsonl_path_dash_encodes_cwd(tmp_path, monkeypatch):
    from mnemo.hooks import session_end

    home = tmp_path / "home"
    claude_dir = home / ".claude" / "projects" / "-home-xyrlan-github-mnemo"
    claude_dir.mkdir(parents=True)
    expected = claude_dir / "sid42.jsonl"
    expected.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows compatibility

    resolved = session_end._resolve_session_jsonl_path("sid42", "/home/xyrlan/github/mnemo")
    assert resolved == expected


def test_resolve_session_jsonl_path_returns_none_when_missing(tmp_home, monkeypatch):
    from mnemo.hooks import session_end

    resolved = session_end._resolve_session_jsonl_path("nonexistent", "/tmp/nowhere")
    assert resolved is None


@pytest.mark.real_spawn
def test_spawn_detached_briefing_posix_uses_start_new_session(monkeypatch, tmp_path):
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

    fake_jsonl = tmp_path / "sid.jsonl"
    fake_jsonl.write_text("{}\n", encoding="utf-8")
    session_end._spawn_detached_briefing(fake_jsonl, "agent_a")

    argv = captured["argv"]
    assert argv[0] == sys.executable
    assert argv[1:4] == ["-m", "mnemo", "briefing"]
    assert str(fake_jsonl) in argv
    assert "agent_a" in argv
    assert captured["kwargs"].get("start_new_session") is True
    assert captured["kwargs"].get("close_fds") is True
    assert captured["kwargs"]["stdin"] == subprocess.DEVNULL


def test_schedule_briefing_no_op_when_disabled(tmp_path, monkeypatch):
    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = {"briefings": {"enabled": False}}

    called = []
    monkeypatch.setattr(session_end, "_spawn_detached_briefing",
                        lambda p, a: called.append((p, a)))

    session_end._maybe_schedule_briefing(cfg, vault, "agent_a", session_id="sid", cwd="/tmp")
    assert called == []


def test_schedule_briefing_spawns_when_enabled_and_jsonl_exists(tmp_path, monkeypatch):
    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    vault.mkdir()

    home = tmp_path / "home"
    jsonl_dir = home / ".claude" / "projects" / "-tmp-cwd"
    jsonl_dir.mkdir(parents=True)
    (jsonl_dir / "sidA.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows compatibility

    cfg = {"briefings": {"enabled": True}}
    called = []
    monkeypatch.setattr(session_end, "_spawn_detached_briefing",
                        lambda p, a: called.append((p, a)))

    session_end._maybe_schedule_briefing(cfg, vault, "agent_a", session_id="sidA", cwd="/tmp/cwd")
    assert len(called) == 1
    # The storage agent is the name main() resolved for the session (#247);
    # cwd only locates the transcript. Pre-#247 this re-resolved from cwd and
    # got "cwd" — the basename of a path with no .git — which is exactly how
    # a removed worktree became an orphan namespace.
    assert called[0][1] == "agent_a"
    assert called[0][0].name == "sidA.jsonl"


def test_schedule_briefing_skips_when_jsonl_missing(tmp_path, tmp_home, monkeypatch):
    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    vault.mkdir()

    cfg = {"briefings": {"enabled": True}}
    called = []
    monkeypatch.setattr(session_end, "_spawn_detached_briefing",
                        lambda p, a: called.append((p, a)))

    session_end._maybe_schedule_briefing(cfg, vault, "agent_a", session_id="missing", cwd="/nowhere")
    assert called == []


def test_schedule_briefing_swallows_popen_errors(tmp_path, monkeypatch):
    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)

    home = tmp_path / "home"
    jsonl_dir = home / ".claude" / "projects" / "-tmp-cwd"
    jsonl_dir.mkdir(parents=True)
    (jsonl_dir / "sid.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows compatibility

    cfg = {"briefings": {"enabled": True}}

    def boom(p, a):
        raise OSError("too many fds")
    monkeypatch.setattr(session_end, "_spawn_detached_briefing", boom)

    session_end._maybe_schedule_briefing(cfg, vault, "agent_a", session_id="sid", cwd="/tmp/cwd")

    errors_log = vault / ".errors.log"
    assert errors_log.exists()
    assert "session_end.briefing" in errors_log.read_text(encoding="utf-8")


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
    mem.write_text("---\ntype: feedback\n---\nbody\n", encoding="utf-8")

    def boom():
        raise OSError("too many fds")
    monkeypatch.setattr(session_end, "_spawn_detached_extraction", boom)

    # Should not raise
    session_end._maybe_schedule_extraction(cfg, vault, "agent_a")

    errors_log = vault / ".errors.log"
    assert errors_log.exists()
    assert "session_end.schedule" in errors_log.read_text(encoding="utf-8")


from unittest.mock import patch

from mnemo.hooks import session_end as se_mod


def test_maybe_schedule_propose_marks_analyzed_on_success(tmp_path):
    with patch("mnemo.autopilot.core.kill_switch.is_active", return_value=True), \
         patch("mnemo.autopilot.proposer.eos_extractor.analyze_session") as analyze, \
         patch("mnemo.core.session.mark_analyzed") as mark:
        se_mod._maybe_schedule_propose(
            cfg={}, vault_root=tmp_path, agent_name="proj-x",
            session_id="sid-success", cwd=str(tmp_path),
        )
    analyze.assert_called_once()
    mark.assert_called_once_with("sid-success")


def test_maybe_schedule_propose_marks_analyzed_when_kill_switch_off(tmp_path):
    """Reaching SessionEnd is the user's intent — respect it even if autopilot off."""
    with patch("mnemo.autopilot.core.kill_switch.is_active", return_value=False), \
         patch("mnemo.core.session.mark_analyzed") as mark:
        se_mod._maybe_schedule_propose(
            cfg={}, vault_root=tmp_path, agent_name="proj-x",
            session_id="sid-off", cwd=str(tmp_path),
        )
    mark.assert_called_once_with("sid-off")


def test_maybe_schedule_propose_swallows_mark_analyzed_failure(tmp_path):
    with patch("mnemo.autopilot.core.kill_switch.is_active", return_value=True), \
         patch("mnemo.autopilot.proposer.eos_extractor.analyze_session"), \
         patch("mnemo.core.session.mark_analyzed", side_effect=OSError("boom")):
        # Must not raise
        se_mod._maybe_schedule_propose(
            cfg={}, vault_root=tmp_path, agent_name="proj-x",
            session_id="sid-mark-fail", cwd=str(tmp_path),
        )


def test_maybe_schedule_propose_keeps_the_cached_project_after_the_tree_is_removed(tmp_path):
    """#247: same defect as the briefing — never re-resolve from a cwd that is gone.

    A dispatched worktree is removed before its child is stopped, so by the
    time SessionEnd runs the cwd has no ``.git`` above it and a fresh
    resolution returns the basename. The project is the name ``main()``
    resolved (session cache first), not a second look at the tree.
    """
    gone = tmp_path / "proj-feature-x"  # removed before SessionEnd; never exists here
    with patch("mnemo.autopilot.core.kill_switch.is_active", return_value=True), \
         patch("mnemo.autopilot.proposer.eos_extractor.analyze_session") as analyze, \
         patch("mnemo.core.session.mark_analyzed"):
        se_mod._maybe_schedule_propose(
            cfg={}, vault_root=tmp_path, agent_name="proj",
            session_id="sid-gone", cwd=str(gone),
        )
    analyze.assert_called_once()
    assert analyze.call_args.kwargs["project"] == "proj"


# --- the sweep the module docstring promises (#176) -------------------------
#
# ``detector`` claimed the sweep rides "every ``mnemo sessions`` invocation and
# the ``session_end`` hook". Only the first was ever true. This makes the
# second one true as well.
#
# It does not close #176: ``session_end`` fires when *this* session ends, so it
# still cannot see an edge that opened and closed inside another session's
# lifetime. It adds a trigger, it does not remove the race.


def test_session_end_sweeps_the_background_session_queue(tmp_path, monkeypatch):
    """The hook calls ``detector.sweep`` with the sessions it can see."""
    from mnemo.hooks import session_end

    calls = []

    def _fake_sweep(sessions, *, vault_root):
        calls.append((sessions, vault_root))
        return 0

    monkeypatch.setattr("mnemo.core.sessions.detector.sweep", _fake_sweep)
    monkeypatch.setattr(
        "mnemo.core.sessions.jobs.read_sessions", lambda **kw: ["s1", "s2"]
    )

    session_end._maybe_sweep_sessions(tmp_path)

    assert calls == [(["s1", "s2"], tmp_path)]


def test_session_end_sweep_is_unscoped(tmp_path, monkeypatch):
    """Every background session, not just this cwd's.

    The hook fires in one repo; the blocked sessions worth recording may be
    running anywhere. ``mnemo sessions`` scopes to the cwd because a human is
    reading a list; the detector is populating state and wants all of it.
    """
    from mnemo.hooks import session_end

    seen = {}

    monkeypatch.setattr("mnemo.core.sessions.detector.sweep", lambda s, **kw: 0)
    monkeypatch.setattr(
        "mnemo.core.sessions.jobs.read_sessions",
        lambda **kw: seen.update(kw) or [],
    )

    session_end._maybe_sweep_sessions(tmp_path)

    assert seen.get("cwd") is None


def test_session_end_sweep_failure_is_swallowed(tmp_path, monkeypatch):
    """A sweep that raises must not take the hook down.

    The queue state file is disposable; the hook's other work is not.
    """
    from mnemo.hooks import session_end

    def _boom(*a, **kw):
        raise RuntimeError("jobs dir vanished")

    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions", _boom)

    session_end._maybe_sweep_sessions(tmp_path)  # must not raise


def test_session_end_consumes_pending_unblocks(tmp_path, monkeypatch):
    """The reader ``pending_unblocks`` never had (#195).

    Spawned detached rather than run inline: consuming a marker briefs and
    extracts, both LLM-bound, and the hook must not hold the session's exit.
    """
    from mnemo.hooks import session_end

    spawned = []

    monkeypatch.setattr(
        "mnemo.core.sessions.detector.pending_unblocks",
        lambda *, vault_root: [{"session_id": "sid-1", "cwd": "/repo"}],
    )
    monkeypatch.setattr(
        session_end, "_spawn_detached_unblock_consumption", lambda: spawned.append(True)
    )

    session_end._maybe_consume_unblocks({"briefings": {"enabled": True}}, tmp_path)

    assert spawned == [True]


def test_session_end_does_not_spawn_when_nothing_is_pending(tmp_path, monkeypatch):
    """The common case. A spawn per session end to find an empty list is a
    process for nothing; the check is a file read."""
    from mnemo.hooks import session_end

    monkeypatch.setattr(
        "mnemo.core.sessions.detector.pending_unblocks", lambda *, vault_root: []
    )
    monkeypatch.setattr(
        session_end,
        "_spawn_detached_unblock_consumption",
        lambda: pytest.fail("must not spawn with nothing pending"),
    )

    session_end._maybe_consume_unblocks({"briefings": {"enabled": True}}, tmp_path)


def test_session_end_respects_briefings_disabled(tmp_path, monkeypatch):
    """Consuming a marker *is* a briefing plus an extraction. A user who
    turned briefings off has opted out of those LLM calls."""
    from mnemo.hooks import session_end

    monkeypatch.setattr(
        "mnemo.core.sessions.detector.pending_unblocks",
        lambda *, vault_root: [{"session_id": "sid-1", "cwd": "/repo"}],
    )
    monkeypatch.setattr(
        session_end,
        "_spawn_detached_unblock_consumption",
        lambda: pytest.fail("must not spawn when briefings are disabled"),
    )

    session_end._maybe_consume_unblocks({"briefings": {"enabled": False}}, tmp_path)


def test_session_end_unblock_failure_is_swallowed(tmp_path, monkeypatch):
    from mnemo.hooks import session_end

    def _boom(*a, **kw):
        raise RuntimeError("queue state vanished")

    monkeypatch.setattr("mnemo.core.sessions.detector.pending_unblocks", _boom)

    session_end._maybe_consume_unblocks({"briefings": {"enabled": True}}, tmp_path)


def test_every_detached_spawn_is_stubbed_by_the_conftest_guard() -> None:
    """#195: the no-spawn guard stubs spawn functions *by name*, so a newly
    added one is not covered and runs for real inside the suite.

    That is how `_spawn_detached_unblock_consumption` slipped through and left
    a real `mnemo sessions --consume-unblocks` child running on the Windows
    runner, which hung the job mid-suite (1926 of ~2800 tests in, then
    KeyboardInterrupt in subprocess.py). 2026-09-02 the same class of leak left
    46 orphan autopilot tuners alive and the machine unusable.

    Asserting the *set* rather than each name means the next spawn added to
    this module fails here instead of on a runner.
    """
    import ast
    from pathlib import Path

    hook = Path(__file__).resolve().parents[2] / "src" / "mnemo" / "hooks" / "session_end.py"
    tree = ast.parse(hook.read_text(encoding="utf-8"))
    spawners = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_spawn_detached")
    }

    conftest = (Path(__file__).resolve().parents[1] / "conftest.py").read_text(encoding="utf-8")
    unstubbed = {name for name in spawners if f"session_end.{name}" not in conftest}

    assert not unstubbed, (
        f"session_end spawn(s) not stubbed by the no-spawn guard: {sorted(unstubbed)}. "
        "Add them to _no_real_detached_jobs in tests/conftest.py or the suite will "
        "launch real detached processes."
    )
