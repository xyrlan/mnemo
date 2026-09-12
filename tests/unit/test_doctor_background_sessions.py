"""``mnemo doctor`` reports on background sessions.

The check exists because a packaging or schema break in this path is
invisible otherwise — the queue would simply print nothing and look
like "no sessions". Doctor exercises the read for real.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.cli.commands.doctor_checks import misc as doctor_misc


@pytest.fixture
def jobs(tmp_path: Path) -> Path:
    """An empty jobs root of our own.

    Not ``tmp_path`` itself: the autouse ``tmp_jobs_dir`` fixture creates
    ``tmp_path/jobs-empty`` there, and counting it as a session would make
    every assertion here off by one.
    """
    root = tmp_path / "jobs"
    root.mkdir()
    return root


def _job(root: Path, short_id: str, **fields) -> None:
    d = root / short_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(fields), encoding="utf-8")


def test_quiet_when_no_background_sessions(jobs: Path, capsys) -> None:
    assert doctor_misc._doctor_check_background_sessions(jobs_root=jobs) is True
    assert "no background sessions" in capsys.readouterr().out


def test_quiet_when_jobs_dir_is_absent(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "nope"
    assert doctor_misc._doctor_check_background_sessions(jobs_root=missing) is True
    assert "no background sessions" in capsys.readouterr().out


def test_reports_counts(jobs: Path, capsys) -> None:
    _job(jobs, "a", state="working", tempo="blocked", needs="q?")
    _job(jobs, "b", state="working", tempo="active")
    _job(jobs, "c", state="done", tempo="idle")

    assert doctor_misc._doctor_check_background_sessions(jobs_root=jobs) is True

    out = capsys.readouterr().out
    assert "3 background session" in out
    assert "1 waiting" in out


def test_unreadable_state_is_reported(jobs: Path, capsys) -> None:
    _job(jobs, "ok", state="working", tempo="active")
    bad = jobs / "bad"
    bad.mkdir()
    (bad / "state.json").write_text("{not json", encoding="utf-8")

    assert doctor_misc._doctor_check_background_sessions(jobs_root=jobs) is True
    assert "1 unreadable" in capsys.readouterr().out


def test_one_session_is_singular(jobs: Path, capsys) -> None:
    _job(jobs, "a", state="working", tempo="active")
    assert doctor_misc._doctor_check_background_sessions(jobs_root=jobs) is True
    assert "1 background session (" in capsys.readouterr().out


# --- Registration: doctor calls every check with the vault path ---
#
# ``DOCTOR_CHECKS`` entries are invoked as ``check_fn(vault)``. A check whose
# only positional parameter was the *jobs* root would silently read the vault
# directory instead: no ``state.json`` anywhere under it, so it would print
# "no background sessions" forever and the break it exists to expose would be
# exactly as invisible as before. These two tests pin the contract.


def test_registered_in_doctor_checks() -> None:
    from mnemo.cli.commands import doctor as doctor_mod

    names = [name for name, _fn in doctor_mod.DOCTOR_CHECKS]
    assert "background_sessions" in names
    assert (
        dict(doctor_mod.DOCTOR_CHECKS)["background_sessions"]
        is doctor_misc._doctor_check_background_sessions
    )


def test_called_with_a_vault_reads_the_real_jobs_dir(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Doctor passes the vault; the check must still consult ``jobs_dir()``."""
    real_jobs = tmp_path / "claude-jobs"
    _job(real_jobs, "a", state="working", tempo="blocked")
    monkeypatch.setattr("mnemo.core.sessions.jobs.jobs_dir", lambda: real_jobs)

    vault = tmp_path / "vault"
    (vault / "shared").mkdir(parents=True)

    assert doctor_misc._doctor_check_background_sessions(vault) is True
    out = capsys.readouterr().out
    assert "1 background session" in out
    assert "1 waiting" in out


# --- Doctor must survive a hostile jobs dir ---


def test_state_json_that_is_a_directory_does_not_raise(jobs: Path, capsys) -> None:
    (jobs / "weird" / "state.json").mkdir(parents=True)

    assert doctor_misc._doctor_check_background_sessions(jobs_root=jobs) is True
    assert "1 unreadable" in capsys.readouterr().out


def test_unlistable_jobs_dir_does_not_raise(jobs: Path, monkeypatch, capsys) -> None:
    real_iterdir = Path.iterdir

    def _boom(self):
        if self == jobs:
            raise PermissionError(13, "Permission denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _boom)

    assert doctor_misc._doctor_check_background_sessions(jobs_root=jobs) is True
    assert "could not read" in capsys.readouterr().out
