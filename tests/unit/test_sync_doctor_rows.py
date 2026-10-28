"""The ``mnemo doctor`` rows for sync (#644): each with sync on, and absent with it off.

The rows render from the summary :mod:`mnemo.core.sync.health` builds, so
most tests hand them a summary and read what they print; the last ones run
the registry adapter on a vault that is really initialised against a bare
repository under ``tmp_path``, and on one that never was.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

import pytest

from mnemo.cli.commands import doctor as doctor_cmd
from mnemo.cli.commands.doctor_checks import sync as rows
from mnemo.core import sync
from mnemo.core.sync import health

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _summary(**over: Any) -> Dict[str, Any]:
    base = {
        "enabled": True, "machineId": "mac", "role": "primary", "primary": "mac",
        "ahead": 0, "behind": 0, "pending": 0, "countsAt": "2026-10-13T10:00:00Z",
        "countsAgeSeconds": 0,
        "lastPull": "2026-10-13T09:59:00Z", "lastPullAgoSeconds": 60, "pullStale": False,
        "lastPush": "2026-10-13T09:58:00Z", "lastPushAgoSeconds": 120, "pushStale": False,
        "pushesRefused": 0, "refused": [], "conflictsResolved": 0, "losersKept": 0,
        "losersDir": "shared/_archive/sync-losers", "formatVersion": 1, "hubFormatVersion": 1,
        "formatSupported": True, "lastError": None, "staleAfterSeconds": 86400,
    }
    base.update(over)
    return base


REACH_OK = {"ok": True, "ms": 42, "message": "reachable (42 ms)"}


def _printed(fn, *args) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        fn(*args)
    return out.getvalue()


def _off(tmp_vault: Path) -> None:
    """With sync off the adapter prints nothing and warns of nothing."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert rows._doctor_check_sync(tmp_vault) is True
    assert out.getvalue() == ""
    assert not (tmp_vault / ".mnemo" / "sync-health.json").exists()


# --- one test per row --------------------------------------------------------


def test_hub_row_says_reachable_and_warns_when_not(tmp_vault):
    assert "ℹ Sync hub: reachable (42 ms)" in _printed(rows._row_hub, _summary(), REACH_OK)
    assert rows._row_hub(_summary(), REACH_OK) is True
    down = {"ok": False, "ms": 3000, "message": "not reachable within 3 s"}
    out = _printed(rows._row_hub, _summary(), down)
    assert "⚠ Sync hub: not reachable within 3 s" in out and "→" in out
    assert rows._row_hub(_summary(), down) is False
    _off(tmp_vault)


def test_role_row_names_role_machine_and_primary(tmp_vault):
    out = _printed(rows._row_role, _summary(role="satellite", machineId="win"), REACH_OK)
    assert "ℹ Sync role: satellite (machine 'win', primary 'mac')" in out
    _off(tmp_vault)


def test_commits_row_counts_ahead_behind_and_pending(tmp_vault):
    out = _printed(rows._row_commits, _summary(ahead=1, pending=3), REACH_OK)
    assert "ℹ Sync commits: 1 ahead, 0 behind (as of the last fetch); 3 files waiting to push" in out
    assert rows._row_commits(_summary(behind=2), REACH_OK) is False
    out = _printed(rows._row_commits, _summary(behind=2), REACH_OK)
    assert "⚠ Sync commits: 0 ahead, 2 behind the hub" in out and "mnemo sync pull" in out
    _off(tmp_vault)


def test_last_pull_and_push_row_warns_past_the_threshold(tmp_vault):
    fresh = _printed(rows._row_last, _summary(), REACH_OK)
    assert "ℹ Sync last pull: 2026-10-13T09:59:00Z (1 min ago); last push: 2026-10-13T09:58:00Z (2 min ago)" in fresh
    stale = _summary(lastPush=None, lastPushAgoSeconds=None, pushStale=True,
                     lastPullAgoSeconds=3 * 86400, pullStale=True)
    assert rows._row_last(stale, REACH_OK) is False
    out = _printed(rows._row_last, stale, REACH_OK)
    assert "⚠ Sync last pull: 2026-10-13T09:59:00Z (3 d ago); last push: never — no successful pull or push in 1 d" in out
    _off(tmp_vault)


def test_error_row_shows_the_last_error_only_when_there_is_one(tmp_vault):
    assert _printed(rows._row_error, _summary(), REACH_OK) == ""
    err = {"at": "2026-10-13T09:00:00Z", "op": "push", "outcome": "error", "message": "hub refused"}
    out = _printed(rows._row_error, _summary(lastError=err), REACH_OK)
    assert "⚠ Sync last error: 2026-10-13T09:00:00Z push: hub refused" in out
    _off(tmp_vault)


def test_refused_row_names_the_file_and_the_shape(tmp_vault):
    assert _printed(rows._row_refused, _summary(), REACH_OK) == ""
    s = _summary(pushesRefused=2, refused=[{"path": "bots/app/memory/MEMORY.md", "kind": "github_token"}])
    assert rows._row_refused(s, REACH_OK) is False
    out = _printed(rows._row_refused, s, REACH_OK)
    assert "⚠ Sync pushes refused by the secret gate: 2; still refused:" in out
    assert "! bots/app/memory/MEMORY.md: github_token" in out
    assert "mnemo redact --apply" in out and "bots/*/memory/" in out
    # Refused once, since cleared: said, but not a warning any more.
    cleared = _summary(pushesRefused=1)
    assert rows._row_refused(cleared, REACH_OK) is True
    assert "refused by the secret gate: 1; nothing refused now" in _printed(rows._row_refused, cleared, REACH_OK)
    _off(tmp_vault)


def test_conflicts_row_says_where_the_losing_versions_are(tmp_vault):
    assert _printed(rows._row_conflicts, _summary(), REACH_OK) == ""
    out = _printed(rows._row_conflicts, _summary(conflictsResolved=3, losersKept=2), REACH_OK)
    assert "ℹ Sync conflicts: 3 resolved for the primary, 2 losing versions kept under shared/_archive/sync-losers/" in out
    _off(tmp_vault)


def test_format_row_warns_of_a_hub_this_mnemo_does_not_know(tmp_vault):
    assert _printed(rows._row_format, _summary(), REACH_OK) == ""
    s = _summary(hubFormatVersion=2, formatSupported=False)
    assert rows._row_format(s, REACH_OK) is False
    out = _printed(rows._row_format, s, REACH_OK)
    assert "⚠ Sync hub format: 2; this mnemo knows 1" in out and "upgraded" in out
    _off(tmp_vault)


def test_state_row_warns_only_when_the_state_file_cannot_be_read(tmp_vault):
    assert _printed(rows._row_state, _summary(), REACH_OK) == ""
    trouble = ".mnemo/sync-state.json holds 361 NUL bytes and nothing else"
    out = _printed(rows._row_state, _summary(stateProblem=trouble), REACH_OK)
    assert f"⚠ Sync state: {trouble}; sync carries on" in out and "→" in out
    assert rows._row_state(_summary(stateProblem=trouble), REACH_OK) is False
    _off(tmp_vault)


def test_locks_row_names_each_lock_a_dead_git_left(tmp_vault):
    assert _printed(rows._row_locks, _summary(gitLocks=[]), REACH_OK) == ""
    held = [{"path": ".git/index.lock", "ageSeconds": 21 * 3600},
            {"path": ".git/refs/heads/main.lock", "ageSeconds": 900}]
    out = _printed(rows._row_locks, _summary(gitLocks=held), REACH_OK)
    assert "⚠ Sync git lock: .git/index.lock left 21 h ago" in out
    assert "⚠ Sync git lock: .git/refs/heads/main.lock left 15 min ago" in out
    assert "→ the next `mnemo sync pull` removes it" in out
    assert rows._row_locks(_summary(gitLocks=held), REACH_OK) is False
    _off(tmp_vault)


def test_the_rows_are_registered_once_in_doctor():
    assert [name for name, _ in doctor_cmd.DOCTOR_CHECKS].count("sync") == 1
    assert [name for name, _ in rows.ROWS] == [
        "hub", "role", "commits", "last", "error", "refused", "conflicts", "format",
        "state", "locks"]


# --- the adapter on a real vault ----------------------------------------------


@pytest.fixture
def git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)


def primary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, machine: str = "mac") -> Path:
    """A vault initialised as the primary of a bare hub under ``tmp_path``,
    with the config the process loads naming it and its machine id."""
    hub = tmp_path / "hub.git"
    subprocess.run(["git", "init", "--bare", "-q", str(hub)], check=True)
    vault = tmp_path / "mac" / "mnemo"
    page = vault / "shared" / "feedback" / "use-the-staging-hub.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\nname: Use the staging hub\ntype: feedback\nsources:\n"
                    "  - bots/app/memory/hub.md\ntags:\n  - deploy\n---\nDeploys read staging.\n",
                    encoding="utf-8")
    (vault / "HOME.md").write_text("# home\n", encoding="utf-8")
    config = vault / "mnemo.config.json"
    config.write_text(json.dumps({"vaultRoot": str(vault), "sync": {"machineId": machine}}),
                      encoding="utf-8")
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(config))
    result = sync.init(vault, str(hub), machine=machine, config_path=config)
    assert result.ok, result.message
    return vault


@needs_git
def test_on_a_vault_that_syncs_the_rows_print_and_off_they_do_not(tmp_path, tmp_vault, monkeypatch, git_env):
    vault = primary(tmp_path, monkeypatch)
    out = _printed(rows._doctor_check_sync, vault)
    assert "ℹ Sync hub: reachable (" in out
    assert "ℹ Sync role: primary (machine 'mac', primary 'mac')" in out
    assert "ℹ Sync commits: 0 ahead, 0 behind (as of the last fetch); 0 files waiting to push" in out
    assert "ℹ Sync last pull: 20" in out and "last push: 20" in out
    assert "⚠" not in out
    assert rows._doctor_check_sync(vault) is True
    _off(tmp_vault)


@needs_git
def test_a_hub_that_does_not_answer_is_a_warning_within_the_bound(tmp_path, monkeypatch, git_env):
    vault = primary(tmp_path, monkeypatch)
    # Pointed at a hub that is not there, as the two-vault tests do. Removing
    # the real one fails on Windows: git's object files are read-only there.
    subprocess.run(["git", "-C", str(vault), "remote", "set-url", "origin",
                    str(tmp_path / "gone.git")], check=True)
    started = datetime.now(timezone.utc)
    out = _printed(rows._doctor_check_sync, vault)
    assert datetime.now(timezone.utc) - started < timedelta(seconds=health.REACH_TIMEOUT + 5)
    assert "⚠ Sync hub: not reachable (" in out
    assert rows._doctor_check_sync(vault) is False


@needs_git
def test_pending_files_and_a_stale_pull_show_on_the_real_summary(tmp_path, monkeypatch, git_env):
    vault = primary(tmp_path, monkeypatch)
    (vault / "bots" / "app" / "briefings" / "sessions").mkdir(parents=True)
    (vault / "bots" / "app" / "briefings" / "sessions" / "s1.md").write_text("# b\n", encoding="utf-8")
    state = json.loads((vault / ".mnemo" / "sync-state.json").read_text(encoding="utf-8"))
    state["lastPull"] = "2026-01-01T00:00:00Z"
    (vault / ".mnemo" / "sync-state.json").write_text(json.dumps(state), encoding="utf-8")
    out = _printed(rows._doctor_check_sync, vault)
    assert "1 file waiting to push" in out
    assert "⚠ Sync last pull: 2026-01-01T00:00:00Z (" in out and "no successful pull in 1 d" in out


def _satellite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A satellite 'win' freshly joined to the hub :func:`primary` made,
    with the config the process loads naming it."""
    primary(tmp_path, monkeypatch)
    vault = tmp_path / "win" / "mnemo"
    config = tmp_path / "win" / "cfg.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"vaultRoot": str(vault), "sync": {"machineId": "win"}}),
                      encoding="utf-8")
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(config))
    result = sync.join(vault, str(tmp_path / "hub.git"), machine="win", config_path=config)
    assert result.ok, result.message
    return vault


@needs_git
def test_a_satellite_that_never_pushed_with_nothing_waiting_is_not_warned(tmp_path, monkeypatch,
                                                                          git_env):
    """#655: one second after `join`, doctor said no push in a day."""
    vault = _satellite(tmp_path, monkeypatch)
    out = _printed(rows._doctor_check_sync, vault)
    assert "ℹ Sync role: satellite (machine 'win', primary 'mac')" in out
    assert "0 files waiting to push" in out
    assert "ℹ Sync last pull: 20" in out and "last push: never" in out
    assert "⚠" not in out
    assert rows._doctor_check_sync(vault) is True


@needs_git
def test_a_satellite_with_files_waiting_past_the_threshold_is_still_warned(tmp_path, monkeypatch,
                                                                           git_env):
    vault = _satellite(tmp_path, monkeypatch)
    sessions = vault / "bots" / "app" / "briefings" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / "s1.md").write_text("# a briefing from win\n", encoding="utf-8")
    health.drop_cache(vault)
    out = _printed(rows._doctor_check_sync, vault)
    assert "1 file waiting to push" in out
    assert "last push: never — no successful push in 1 d" in out
    assert rows._doctor_check_sync(vault) is False

    state_file = vault / ".mnemo" / "sync-state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["lastPush"] = "2026-01-01T00:00:00Z"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    out = _printed(rows._doctor_check_sync, vault)
    assert "last push: 2026-01-01T00:00:00Z (" in out and "no successful push in 1 d" in out


def _aged(path: Path, seconds: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    then = time.time() - seconds
    os.utime(str(path), (then, then))
    return path


@needs_git
def test_a_zeroed_state_and_a_dead_index_lock_show_on_a_real_vault(tmp_path, monkeypatch, git_env):
    """#735: a BSOD zeroed the state and left ``index.lock``; doctor said nothing."""
    vault = primary(tmp_path, monkeypatch)
    (vault / ".mnemo" / "sync-state.json").write_bytes(b"\0" * 361)
    # Off the hour: an mtime set to exactly 21 h ago can read back a hair
    # younger on Windows, and the age floors to "20 h".
    _aged(vault / ".git" / "index.lock", 21 * 3600 + 600)
    out = _printed(rows._doctor_check_sync, vault)
    assert "⚠ Sync state: .mnemo/sync-state.json holds 361 NUL bytes and nothing else" in out
    assert "⚠ Sync git lock: .git/index.lock left 21 h ago" in out
    assert rows._doctor_check_sync(vault) is False

    assert sync.pull(vault, machine="mac").outcome == "ok"
    out = _printed(rows._doctor_check_sync, vault)
    assert "Sync state" not in out and "Sync git lock" not in out


def test_an_unreadable_state_where_sync_never_ran_is_a_warning_not_silence(tmp_vault):
    (tmp_vault / ".git").mkdir()
    (tmp_vault / ".mnemo").mkdir(exist_ok=True)
    (tmp_vault / ".mnemo" / "sync-state.json").write_bytes(b"\0" * 12)
    out = _printed(rows._doctor_check_sync, tmp_vault)
    assert "⚠ Sync state: .mnemo/sync-state.json holds 12 NUL bytes and nothing else" in out
    assert "sync reads as off here" in out
    assert rows._doctor_check_sync(tmp_vault) is False


@needs_git
def test_a_satellite_names_what_it_withholds_and_does_not_count_it_as_waiting(
        tmp_path, monkeypatch, git_env):
    """#756: 92 "files waiting to push" on a satellite whose every push said
    ``nothing``: changes a satellite may not publish, which no push carries."""
    vault = _satellite(tmp_path, monkeypatch)
    memory = vault / "bots" / "app" / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    (memory / "new-note.md").write_text("- added on the satellite\n", encoding="utf-8")
    assert sync.push(vault, machine="win").outcome == "nothing"
    health.drop_cache(vault)

    summary = health.summary(vault, "win", max_age=0)
    assert (summary["pending"], summary["withheld"]) == (0, 1)
    assert health.segment(summary) == ""
    out = _printed(rows._doctor_check_sync, vault)
    assert "0 files waiting to push, 1 withheld (change a satellite may not publish" in out
    assert "⚠" not in out

    status = sync.status(vault, machine="win")
    assert (status["pendingFiles"], status["withheldFiles"]) == (0, 1)

    sessions = vault / "bots" / "app" / "briefings" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / "s1.md").write_text("# a briefing from win\n", encoding="utf-8")
    health.drop_cache(vault)
    summary = health.summary(vault, "win", max_age=0)
    assert (summary["pending"], summary["withheld"]) == (1, 1)
