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
    ledger.mark_install_run_done(vault)

    assert check_mod._doctor_check_install_backfill(vault) is True
    assert capsys.readouterr().out == ""


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


def test_a_stale_lock_is_reported_even_when_the_run_completed(vault, capsys):
    """A leaked lock still blocks future sessions; say so regardless."""
    ledger.mark_install_run_done(vault)
    ledger.acquire_spawn_lock(vault)
    _age_the_lock(vault, ledger.SPAWN_LOCK_TTL_SECONDS + 3600)

    assert check_mod._doctor_check_install_backfill(vault) is False
    assert "never finished" in capsys.readouterr().out


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
