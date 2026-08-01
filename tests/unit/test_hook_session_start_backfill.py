"""session_start fires the install backfill exactly once, detached, in the repo.

Three things have to be true or the feature silently does nothing for the user
it exists to help:

1. it fires without being asked, on the first session after install;
2. it fires *once* — a sweep restarting every session is worse than none;
3. it fires **in the session's repo**. ``mnemo backfill --install-run`` picks
   its project from ``os.getcwd()``, so the child's working directory is what
   decides which repo gets backfilled. A spawn that inherits the hook's own
   cwd backfills whatever directory Claude Code happened to launch the hook
   from, which is not necessarily the session's.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mnemo.core.backfill import ledger
from mnemo.hooks import session_start


# conftest's autouse guard replaces the module attribute so no test ever really
# spawns a sweep. The tests below that exercise the spawn itself need the real
# function, so bind it here — at import, before any fixture runs.
_REAL_SPAWN = session_start._spawn_detached_backfill


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    return root


def _cfg(vault: Path, **over) -> dict:
    bf = {"enabled": True, "installCap": 20, "minFileMutations": 1,
          "autoOnFirstSession": True}
    bf.update(over)
    return {"vaultRoot": str(vault), "backfill": bf}


def _recorder(monkeypatch) -> list[dict]:
    """Replace the spawn with a recorder of the kwargs it was handed."""
    calls: list[dict] = []
    monkeypatch.setattr(
        session_start, "_spawn_detached_backfill", lambda **kw: calls.append(kw)
    )
    return calls


def test_the_suite_wide_spawn_guard_is_active():
    """Pin conftest's autouse guard.

    Without it, every test that runs ``session_start.main()`` launches a real
    sweep against the developer's own vault and the hook's own except clause
    hides it. Nothing else in the suite can notice that, because the symptom
    is a background process, not a failure.
    """
    assert session_start._spawn_detached_backfill is not _REAL_SPAWN


# --- scheduling ------------------------------------------------------------

def test_spawns_once_and_marks_the_ledger(vault, monkeypatch):
    spawned = _recorder(monkeypatch)

    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")
    assert len(spawned) == 1
    assert ledger.load(vault)["installRunDone"] is True


def test_second_session_does_not_spawn_again(vault, monkeypatch):
    spawned = _recorder(monkeypatch)

    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")
    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")
    assert len(spawned) == 1


def test_disabled_auto_flag_never_spawns(vault, monkeypatch):
    spawned = _recorder(monkeypatch)

    session_start._maybe_schedule_install_backfill(
        _cfg(vault, autoOnFirstSession=False), vault, cwd="/repo",
    )
    assert spawned == []


def test_disabled_backfill_never_spawns(vault, monkeypatch):
    spawned = _recorder(monkeypatch)

    session_start._maybe_schedule_install_backfill(
        _cfg(vault, enabled=False), vault, cwd="/repo",
    )
    assert spawned == []


def test_a_declined_run_leaves_the_ledger_untouched(vault, monkeypatch):
    """Turning the flag off must not consume the one-shot marker.

    Otherwise flipping ``autoOnFirstSession`` back on would never fire, because
    the session that skipped it already claimed the only run.
    """
    _recorder(monkeypatch)

    session_start._maybe_schedule_install_backfill(
        _cfg(vault, autoOnFirstSession=False), vault, cwd="/repo",
    )
    assert ledger.load(vault)["installRunDone"] is False


def test_a_spawn_failure_is_swallowed(vault, monkeypatch):
    def boom(**_kw):
        raise OSError("no fork for you")

    monkeypatch.setattr(session_start, "_spawn_detached_backfill", boom)
    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")


def test_a_spawn_failure_does_not_burn_the_one_shot(vault, monkeypatch):
    """A spawn that never launched must stay retryable.

    The marker is written before the spawn so a crash *inside* the harvest
    cannot restart the sweep every session. A failed ``Popen`` is the other
    case entirely: nothing ran, so the user would silently never get a
    backfill at all — the exact outcome this feature exists to prevent.
    """
    calls: list[str] = []

    def boom(**_kw):
        calls.append("x")
        raise OSError("no fork for you")

    monkeypatch.setattr(session_start, "_spawn_detached_backfill", boom)
    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")
    assert ledger.load(vault)["installRunDone"] is False

    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")
    assert len(calls) == 2, "a failed spawn should be retried next session"


def test_a_preexisting_done_marker_survives_a_disabled_flag(vault, monkeypatch):
    """Rollback must not resurrect a run that already happened."""
    spawned = _recorder(monkeypatch)
    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")

    session_start._maybe_schedule_install_backfill(
        _cfg(vault, autoOnFirstSession=False), vault, cwd="/repo",
    )
    assert ledger.load(vault)["installRunDone"] is True
    assert len(spawned) == 1


def test_ledger_sessions_are_preserved_when_marking(vault, monkeypatch):
    """The marker is a read-modify-write, not a clobber of the harvest record."""
    _recorder(monkeypatch)
    ledger.save(vault, {"schemaVersion": 1, "sessions": {"abc": {"status": "done"}},
                        "installRunDone": False})

    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")

    led = ledger.load(vault)
    assert led["installRunDone"] is True
    assert led["sessions"]["abc"]["status"] == "done"


# --- the cwd the child actually gets ---------------------------------------

def test_the_scheduled_spawn_carries_the_session_cwd(vault, monkeypatch):
    spawned = _recorder(monkeypatch)

    session_start._maybe_schedule_install_backfill(
        _cfg(vault), vault, cwd="/Users/me/github/theproject",
    )
    assert spawned[0]["cwd"] == "/Users/me/github/theproject"


def test_popen_is_given_the_cwd_not_the_hooks_own(monkeypatch, tmp_path):
    """`backfill --install-run` resolves its project from os.getcwd().

    Popen inherits the parent's cwd unless told otherwise, so the session's
    directory has to be passed through explicitly — otherwise the sweep
    backfills whichever repo the hook process happens to be sitting in.
    """
    repo = tmp_path / "theproject"
    repo.mkdir()
    seen: dict = {}

    def fake_popen(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.chdir(tmp_path)  # a *different* directory from the session's

    _REAL_SPAWN(cwd=str(repo))

    assert seen["kwargs"]["cwd"] == str(repo)
    assert seen["kwargs"]["cwd"] != str(tmp_path)


def test_a_missing_cwd_falls_back_to_inheriting(monkeypatch):
    """No cwd known → don't pass one; inheriting beats guessing."""
    seen: dict = {}

    def fake_popen(argv, **kwargs):
        seen["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    _REAL_SPAWN(cwd=None)
    assert seen["kwargs"].get("cwd") is None


def test_a_vanished_cwd_is_not_passed_through(monkeypatch, tmp_path):
    """A cwd that no longer exists would make Popen raise before the child ran."""
    seen: dict = {}

    def fake_popen(argv, **kwargs):
        seen["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    _REAL_SPAWN(cwd=str(tmp_path / "gone"))
    assert seen["kwargs"].get("cwd") is None


# --- the spawn itself ------------------------------------------------------

def test_the_child_is_the_capped_install_run(monkeypatch):
    seen: dict = {}

    def fake_popen(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    _REAL_SPAWN(cwd=None)

    assert seen["argv"][-2:] == ["backfill", "--install-run"]
    assert seen["kwargs"]["stdin"] is subprocess.DEVNULL
    assert seen["kwargs"]["stdout"] is subprocess.DEVNULL
    assert seen["kwargs"]["stderr"] is subprocess.DEVNULL


def test_posix_detaches_with_a_new_session(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(subprocess, "Popen",
                        lambda argv, **kw: seen.update(kw) or object())
    monkeypatch.setattr(sys, "platform", "linux")

    _REAL_SPAWN(cwd=None)

    assert seen["start_new_session"] is True
    assert "creationflags" not in seen


def test_windows_detaches_with_creationflags(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(subprocess, "Popen",
                        lambda argv, **kw: seen.update(kw) or object())
    monkeypatch.setattr(sys, "platform", "win32")

    _REAL_SPAWN(cwd=None)

    assert seen["creationflags"] == 0x00000008 | 0x00000200
    assert "start_new_session" not in seen


# --- wired into the hook ---------------------------------------------------

def _run_hook(monkeypatch, vault: Path, cwd: Path) -> None:
    payload = {"session_id": "s1", "cwd": str(cwd), "source": "startup"}
    monkeypatch.setattr("json.load", lambda _f: dict(payload))
    monkeypatch.setattr(
        "mnemo.core.paths.vault_root", lambda _cfg=None: vault, raising=False
    )
    session_start.main()


def test_main_schedules_the_backfill_with_the_payload_cwd(
    monkeypatch, tmp_path, tmp_home
):
    repo = tmp_path / "theproject"
    repo.mkdir()
    vault = tmp_path / "vault"
    seen: list[dict] = []
    monkeypatch.setattr(
        session_start, "_spawn_detached_backfill", lambda **kw: seen.append(kw)
    )
    monkeypatch.chdir(tmp_path)

    _run_hook(monkeypatch, vault, repo)

    assert seen and seen[0]["cwd"] == str(repo), "hook must pass the session's cwd"
    assert ledger.load(vault)["installRunDone"] is True


def test_main_only_schedules_it_on_the_first_session(monkeypatch, tmp_path, tmp_home):
    repo = tmp_path / "theproject"
    repo.mkdir()
    vault = tmp_path / "vault"
    seen: list[dict] = []
    monkeypatch.setattr(
        session_start, "_spawn_detached_backfill", lambda **kw: seen.append(kw)
    )

    _run_hook(monkeypatch, vault, repo)
    _run_hook(monkeypatch, vault, repo)

    assert len(seen) == 1


def test_main_still_emits_clean_json_on_stdout(monkeypatch, tmp_path, tmp_home, capsys):
    """The spawn must not put anything on stdout — it carries the injection envelope."""
    repo = tmp_path / "theproject"
    repo.mkdir()
    vault = tmp_path / "vault"
    monkeypatch.setattr(session_start, "_spawn_detached_backfill", lambda **kw: None)

    _run_hook(monkeypatch, vault, repo)

    out = capsys.readouterr().out
    if out.strip():
        json.loads(out)  # must parse; anything the spawn printed would break it
