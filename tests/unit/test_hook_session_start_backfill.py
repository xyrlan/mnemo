"""session_start fires the install backfill once, detached, in the session's repo.

Four things have to be true or the feature silently does nothing for the user
it exists to help:

1. it fires without being asked, on the first session after install;
2. it does not fire twice — not on the next session, and not from a second
   window opened in the same instant;
3. it fires **in the session's repo**. ``mnemo backfill --install-run`` picks
   its project from ``os.getcwd()``, so the child's working directory is what
   decides which repo gets backfilled. A spawn that inherits the hook's own
   cwd backfills whatever directory Claude Code happened to launch the hook
   from, which is not necessarily the session's;
4. a run that never happened stays retryable. The hook holds a *spawn lock*
   and nothing more — ``installRunDone`` is the CLI's to write, and only for
   a sweep that reached the end (see test_cli_backfill.py).
"""
from __future__ import annotations

import io
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

def test_spawns_once_and_takes_the_spawn_lock(vault, monkeypatch):
    spawned = _recorder(monkeypatch)

    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")
    assert len(spawned) == 1
    assert ledger.spawn_lock_path(vault).exists()


def test_the_hook_never_claims_the_run_completed(vault, monkeypatch):
    """``installRunDone`` means a sweep finished, and the hook cannot know that.

    It only launches a child whose entire output goes to DEVNULL. Writing the
    marker here is what made an environmental abort — no ``claude`` CLI,
    expired auth, rate limit — permanently spend the user's one automatic run
    while doing zero work and saying nothing.
    """
    _recorder(monkeypatch)

    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")
    assert ledger.load(vault)["installRunDone"] is False


def test_a_completed_run_is_never_repeated(vault, monkeypatch):
    spawned = _recorder(monkeypatch)
    ledger.mark_install_run_done(vault)

    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")
    assert spawned == []


def test_second_session_does_not_spawn_while_one_is_in_flight(vault, monkeypatch):
    spawned = _recorder(monkeypatch)

    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")
    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")
    assert len(spawned) == 1


def test_a_finished_run_that_released_its_lock_is_still_not_repeated(vault, monkeypatch):
    """Lock released + marker set is the normal end state. It must stay quiet."""
    spawned = _recorder(monkeypatch)
    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")
    ledger.release_spawn_lock(vault)
    ledger.mark_install_run_done(vault)

    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")
    assert len(spawned) == 1


def test_a_lock_orphaned_by_a_finished_run_is_reaped(vault, monkeypatch):
    """A lock left behind after the marker is set gates nothing, so drop it.

    The ``installRunDone`` check returns before ``acquire_spawn_lock`` is ever
    called, and ``acquire`` is the only code that reaps a lock by TTL. So a
    lock leaked by a killed child — after its sweep had already marked itself
    done — is immortal: no future session consults it, nothing removes it, and
    doctor reports "a backfill started N hours ago and never finished" forever
    about a sweep that finished. This hook is the lock's only consumer, so it
    is the only thing that can honestly retire it.
    """
    spawned = _recorder(monkeypatch)
    ledger.acquire_spawn_lock(vault)
    ledger.mark_install_run_done(vault)

    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")

    assert spawned == []  # still no second sweep
    assert not ledger.spawn_lock_path(vault).exists()


def test_reaping_the_orphan_lock_does_not_need_it_to_be_stale(vault, monkeypatch):
    """Age is irrelevant once the marker is set — nothing can be in flight.

    ``cmd_backfill`` writes the marker and *then* releases, so a lock that
    coexists with the marker belongs to a process that is already past its
    work. Waiting out a six-hour TTL to remove it would only prolong a warning
    that was never true.
    """
    _recorder(monkeypatch)
    ledger.acquire_spawn_lock(vault)
    ledger.mark_install_run_done(vault)
    assert ledger.spawn_lock_age(vault) < ledger.SPAWN_LOCK_TTL_SECONDS

    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")
    assert not ledger.spawn_lock_path(vault).exists()


def test_an_aborted_run_that_released_its_lock_is_retried(vault, monkeypatch):
    """The whole point of the split: no marker means try again.

    An environmental abort releases the lock and records nothing, so the next
    session gets another go. That costs one fast failed ``claude`` invocation
    and no LLM call — the right price for not silently abandoning the feature.
    """
    spawned = _recorder(monkeypatch)
    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")
    ledger.release_spawn_lock(vault)  # what cmd_backfill's finally does

    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")
    assert len(spawned) == 2


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


def test_a_declined_run_takes_no_lock(vault, monkeypatch):
    """Turning the flag off must not consume the one-shot.

    Otherwise flipping ``autoOnFirstSession`` back on would be blocked by a
    lock the declined session took and no child will ever release.
    """
    _recorder(monkeypatch)

    session_start._maybe_schedule_install_backfill(
        _cfg(vault, autoOnFirstSession=False), vault, cwd="/repo",
    )
    assert not ledger.spawn_lock_path(vault).exists()


def test_a_spawn_failure_releases_the_lock_and_is_swallowed(vault, monkeypatch):
    """A spawn that never launched must not hold the lock, and must not raise.

    Nothing ran, so the user would silently never get a backfill at all — the
    exact outcome this feature exists to prevent.
    """
    calls: list[str] = []

    def boom(**_kw):
        calls.append("x")
        raise OSError("no fork for you")

    monkeypatch.setattr(session_start, "_spawn_detached_backfill", boom)
    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")
    assert not ledger.spawn_lock_path(vault).exists()

    session_start._maybe_schedule_install_backfill(_cfg(vault), vault, cwd="/repo")
    assert len(calls) == 2, "a failed spawn should be retried next session"


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
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(
        "mnemo.core.paths.vault_root", lambda _cfg=None: vault, raising=False
    )
    # autoOnFirstSession defaults to False (opt-in); these tests exercise the
    # auto-scheduling wiring itself, so opt in explicitly via a config file.
    config_path = vault / ".mnemo" / "mnemo.config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"backfill": {"autoOnFirstSession": True}}), encoding="utf-8"
    )
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(config_path))
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
    assert ledger.spawn_lock_path(vault).exists()


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
    """stdout is the injection envelope's channel and the spawn must not touch it.

    The envelope is forced into existence rather than hoped for — a version of
    this test that only checked stdout *if* something was written asserted
    nothing at all, because on a bare vault nothing is.
    """
    repo = tmp_path / "theproject"
    repo.mkdir()
    vault = tmp_path / "vault"
    monkeypatch.setattr(session_start, "_spawn_detached_backfill", lambda **kw: None)
    monkeypatch.setattr(
        session_start, "_build_injection_payload",
        lambda *a, **k: "mnemo://v1\nlocal: [testing]",
    )

    _run_hook(monkeypatch, vault, repo)

    out = capsys.readouterr().out
    assert out.strip(), "the envelope must be on stdout for this test to mean anything"
    parsed = json.loads(out)
    assert parsed["hookSpecificOutput"]["additionalContext"] == (
        "mnemo://v1\nlocal: [testing]"
    )
