"""Doctor is the only way anyone finds out the automatic backfill failed.

The child's stdout and stderr are DEVNULL, the hook swallows exceptions, and
the spec's "its summary surfaces on the next session start" was never built.
Without this check, an install run that aborts on a missing `claude` CLI is
completely silent — the user just keeps getting an empty vault.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from mnemo.cli.commands.doctor_checks import install_backfill as check_mod
from mnemo.core import errors as err_mod
from mnemo.core.backfill import ledger


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    monkeypatch.setattr(
        "mnemo.core.config.load_config",
        lambda: {"vaultRoot": str(root), "backfill": {"enabled": True}},
    )
    return root


def _log_failure(vault: Path, where: str = "backfill.harvest") -> None:
    err_mod.log_error(vault, where, RuntimeError("claude: command not found"))


def _sweep(vault: Path, *, done: list[int] = (), failed: int = 0) -> None:
    """Write the ledger a real sweep would leave behind.

    ``done`` is one produced-count per successfully harvested session.
    """
    led = ledger.load(vault)
    sessions = led.setdefault("sessions", {})
    for i, produced in enumerate(done):
        sessions[f"ok-{i}"] = {"status": "done", "hash": "sha256:x",
                               "produced": produced, "attempts": 0}
    for i in range(failed):
        sessions[f"bad-{i}"] = {"status": "failed", "hash": "sha256:x",
                                "attempts": 1, "lastError": "timed out twice"}
    ledger.save(vault, led)


def _age_the_lock(vault: Path, seconds: float) -> None:
    path = ledger.spawn_lock_path(vault)
    when = time.time() - seconds
    os.utime(path, (when, when))


def test_an_aborted_run_is_reported(vault, capsys):
    """No marker, no lock, errors logged — the environmental-abort shape."""
    _log_failure(vault)

    assert check_mod._doctor_check_install_backfill(vault) is False
    out = capsys.readouterr().out
    assert "failed 1 time and never completed" in out
    assert "mnemo backfill" in out


def test_repeated_aborts_are_counted(vault, capsys):
    for _ in range(3):
        _log_failure(vault)

    assert check_mod._doctor_check_install_backfill(vault) is False
    assert "failed 3 times" in capsys.readouterr().out


def test_a_spawn_that_never_launched_is_reported_too(vault, capsys):
    """The hook swallows Popen failures; this is the only trace they leave."""
    _log_failure(vault, where="session_start.backfill")

    assert check_mod._doctor_check_install_backfill(vault) is False
    assert "never completed" in capsys.readouterr().out


def test_a_completed_run_is_silent(vault, capsys):
    _log_failure(vault)
    _sweep(vault, done=[2])
    ledger.mark_install_run_done(vault)

    assert check_mod._doctor_check_install_backfill(vault) is True
    assert capsys.readouterr().out == ""


# --- the sweep that finished, failed, and harvested nothing -----------------
#
# Attributable failures (a transcript that times out) are stepped over rather
# than aborting, one per session up to installCap. Twenty of them is a run
# that reaches the end, marks itself done, and leaves the vault as empty as it
# found it. `--retry-failed` is the remedy and nothing names it.

def test_a_sweep_that_produced_nothing_but_failed_is_reported(vault, capsys):
    _sweep(vault, failed=20)
    ledger.mark_install_run_done(vault)

    assert check_mod._doctor_check_install_backfill(vault) is False
    out = capsys.readouterr().out
    assert "harvested nothing — 20 sessions failed" in out
    assert "--retry-failed" in out


def test_one_failed_session_reads_as_singular(vault, capsys):
    _sweep(vault, failed=1)
    ledger.mark_install_run_done(vault)

    assert check_mod._doctor_check_install_backfill(vault) is False
    assert "1 session failed" in capsys.readouterr().out


def test_a_sweep_that_produced_something_is_silent(vault, capsys):
    """Partial success is success — the vault is no longer empty."""
    _sweep(vault, done=[1], failed=19)
    ledger.mark_install_run_done(vault)

    assert check_mod._doctor_check_install_backfill(vault) is True
    assert capsys.readouterr().out == ""


def test_a_sweep_of_quiet_sessions_is_silent(vault, capsys):
    """Zero produced with zero failures is a correct, complete sweep.

    Sessions below the mutation threshold, or with nothing worth keeping,
    legitimately yield nothing. Only the *combination* with failures means
    the user has an empty vault and an unread reason for it.
    """
    _sweep(vault, done=[0, 0, 0])
    ledger.mark_install_run_done(vault)

    assert check_mod._doctor_check_install_backfill(vault) is True
    assert capsys.readouterr().out == ""


def test_a_machine_with_no_history_to_sweep_is_silent(vault, capsys):
    """The fresh-install case: swept, nothing to harvest, marker set, no records."""
    ledger.mark_install_run_done(vault)

    assert check_mod._doctor_check_install_backfill(vault) is True
    assert capsys.readouterr().out == ""


def test_a_hand_edited_ledger_stays_quiet(vault, capsys):
    ledger.save(vault, {"schemaVersion": 1, "sessions": "not a dict",
                        "installRunDone": True})

    assert check_mod._doctor_check_install_backfill(vault) is True
    assert capsys.readouterr().out == ""


def test_a_barren_sweep_is_reported_without_any_error_log(vault, capsys):
    """The ledger is the source of truth here, not .errors.log.

    The two fingerprints are independent: this one has to fire on ledger
    evidence alone, or it would just be the abort check wearing a hat.
    """
    _sweep(vault, failed=3)
    ledger.mark_install_run_done(vault)
    assert not (vault / err_mod.ERROR_LOG_NAME).exists()

    assert check_mod._doctor_check_install_backfill(vault) is False
    assert "harvested nothing" in capsys.readouterr().out


def test_a_sweep_in_flight_is_silent(vault, capsys):
    """Warning about a backfill that is running right now is just noise."""
    _log_failure(vault)
    ledger.acquire_spawn_lock(vault)

    assert check_mod._doctor_check_install_backfill(vault) is True
    assert capsys.readouterr().out == ""


def test_a_killed_sweep_is_reported_once_its_lock_goes_stale(vault, capsys):
    """The failure mode that leaves no error entry, only an abandoned lock."""
    ledger.acquire_spawn_lock(vault)
    _age_the_lock(vault, ledger.SPAWN_LOCK_TTL_SECONDS + 3600)

    assert check_mod._doctor_check_install_backfill(vault) is False
    out = capsys.readouterr().out
    assert "never finished" in out
    assert "mnemo backfill" in out


def test_a_stale_lock_on_a_completed_run_is_not_a_failure(vault, capsys):
    """The marker outranks the lock: that sweep *did* finish.

    Reporting "started N hours ago and never finished" about a run that wrote
    its completion marker is false on both halves, and the remedy it prints is
    worse: ``mnemo backfill`` answers "nothing to do — every transcript is
    already harvested" and never touches the lock, so following the advice
    changes nothing and the warning returns forever. The leak is real but
    inert — nothing consults that lock again — and the next session start
    reaps it.
    """
    ledger.mark_install_run_done(vault)
    ledger.acquire_spawn_lock(vault)
    _age_the_lock(vault, ledger.SPAWN_LOCK_TTL_SECONDS + 3600)

    assert check_mod._doctor_check_install_backfill(vault) is True
    assert capsys.readouterr().out == ""


def test_a_stale_lock_on_a_completed_run_does_not_mask_a_barren_sweep(vault, capsys):
    """Deferring to the marker must not skip the checks that live behind it."""
    _sweep(vault, failed=4)
    ledger.mark_install_run_done(vault)
    ledger.acquire_spawn_lock(vault)
    _age_the_lock(vault, ledger.SPAWN_LOCK_TTL_SECONDS + 3600)

    assert check_mod._doctor_check_install_backfill(vault) is False
    assert "harvested nothing" in capsys.readouterr().out


def test_a_vault_that_has_simply_never_run_it_is_silent(vault, capsys):
    """A fresh install whose first session has not ended is not a failure.

    This is why the check looks for evidence of failure rather than for the
    absence of success — and why it never scans ~/.claude/projects, which
    would make the result depend on the machine instead of the vault.
    """
    assert check_mod._doctor_check_install_backfill(vault) is True
    assert capsys.readouterr().out == ""


def test_unrelated_errors_in_the_log_are_ignored(vault, capsys):
    _log_failure(vault, where="session_start.mirror")
    _log_failure(vault, where="extract.cluster")

    assert check_mod._doctor_check_install_backfill(vault) is True
    assert capsys.readouterr().out == ""


def test_a_corrupt_error_log_line_is_skipped(vault, capsys):
    (vault / err_mod.ERROR_LOG_NAME).write_bytes(b"{not json\n")
    _log_failure(vault)

    assert check_mod._doctor_check_install_backfill(vault) is False
    assert "failed 1 time" in capsys.readouterr().out


def test_a_disabled_backfill_is_silent(vault, monkeypatch, capsys):
    _log_failure(vault)
    monkeypatch.setattr(
        "mnemo.core.config.load_config",
        lambda: {"vaultRoot": str(vault), "backfill": {"enabled": False}},
    )
    assert check_mod._doctor_check_install_backfill(vault) is True
    assert capsys.readouterr().out == ""


def test_the_check_never_breaks_doctor(vault, monkeypatch):
    """Every other doctor check is fail-silent; this one is no exception."""
    monkeypatch.setattr(
        "mnemo.core.config.load_config",
        lambda: (_ for _ in ()).throw(OSError("config on fire")),
    )
    assert check_mod._doctor_check_install_backfill(vault) is True


def test_the_check_is_registered_with_doctor():
    """An unregistered check is a check that never runs."""
    from mnemo.cli.commands.doctor import DOCTOR_CHECKS

    names = [name for name, _fn in DOCTOR_CHECKS]
    assert "install_backfill" in names
    fns = [fn for _n, fn in DOCTOR_CHECKS]
    assert check_mod._doctor_check_install_backfill in fns
