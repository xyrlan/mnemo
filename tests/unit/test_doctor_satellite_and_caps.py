"""#756: ``status`` and ``doctor`` on a sync satellite, and doctor's long lists.

A satellite never extracts, so its own ``last-auto-run.json`` is not its
current state: what it reports is when the primary last extracted, from the
extraction state every pull brings. A per-item list shows its first few
items unless ``mnemo doctor --all`` asked for every one.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from mnemo.cli.commands import status as status_cmd
from mnemo.cli.commands.doctor_checks import _lists, child_reports, misc
from mnemo.cli.commands.doctor_checks import sync as sync_rows
from mnemo.core.sync import state as sync_state

AUTO = {"extraction": {"auto": {"enabled": True}}}


def _stamp(days_ago: float) -> str:
    return (datetime.now() - timedelta(days=days_ago)).replace(microsecond=0).isoformat()


@pytest.fixture
def satellite(tmp_path, monkeypatch):
    """A vault whose role is satellite of 'mac', with a failed local run from
    before it joined, last pulled at a known time."""
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    monkeypatch.setattr("mnemo.core.config.load_config", lambda *a, **k: AUTO)
    monkeypatch.setattr("mnemo.core.sync.roles.is_satellite", lambda *a, **k: True)
    sync_state.write_primary(vault, "mac")
    (vault / ".mnemo" / "sync-state.json").write_text(
        json.dumps({"enabled": True, "lastPull": "2026-10-28T13:24:54Z"}), encoding="utf-8")
    (vault / ".mnemo" / "last-auto-run.json").write_text(json.dumps({
        "run_id": _stamp(8), "finished_at": _stamp(8), "exit_code": 1,
        "summary": {"failed_chunks": 1},
        "error": {"type": "LLMSubprocessError", "message": "one chunk failed"},
    }), encoding="utf-8")
    return vault


def _extracted(vault: Path, last_run: str) -> None:
    (vault / ".mnemo" / "extraction-state.json").write_text(
        json.dumps({"schema_version": 2, "last_run": last_run, "entries": {}}), encoding="utf-8")


# --- auto-brain on a satellite ----------------------------------------------


def test_doctor_on_a_satellite_reports_the_primary_and_not_its_own_old_failure(satellite, capsys):
    _extracted(satellite, _stamp(1))
    assert misc._doctor_check_auto_brain(satellite) is True
    out = capsys.readouterr().out
    assert "FAILED" not in out and "7+ days" not in out and "⚠" not in out
    assert "ℹ Auto-brain: this machine is a sync satellite; extraction runs on the primary 'mac'" in out
    assert "as of this machine's last pull 2026-10-28T13:24:54Z" in out


def test_doctor_on_a_satellite_still_reports_a_primary_silent_for_a_week(satellite, capsys):
    stamp = _stamp(10)
    _extracted(satellite, stamp)
    assert misc._doctor_check_auto_brain(satellite) is False
    out = capsys.readouterr().out
    assert f"which has not extracted in 7+ days (last: {stamp}" in out
    assert "FAILED" not in out


def test_doctor_on_a_satellite_with_no_extraction_pulled_says_so(satellite, capsys):
    assert misc._doctor_check_auto_brain(satellite) is True
    out = capsys.readouterr().out
    assert "no extraction of it has arrived here (last pull: 2026-10-28T13:24:54Z)" in out


def test_the_primary_still_reports_its_own_failed_run(satellite, monkeypatch, capsys):
    monkeypatch.setattr("mnemo.core.sync.roles.is_satellite", lambda *a, **k: False)
    _extracted(satellite, _stamp(1))
    assert misc._doctor_check_auto_brain(satellite) is False
    out = capsys.readouterr().out
    assert "⚠ Auto-brain: FAILED on last run: LLMSubprocessError" in out
    assert "7+ days" in out


def test_status_on_a_satellite_names_the_primary_run_not_the_local_one(satellite, capsys):
    stamp = _stamp(1)
    _extracted(satellite, stamp)
    status_cmd._print_auto_brain_status(satellite)
    out = capsys.readouterr().out
    assert "FAILED" not in out and "last run:" not in out
    assert "extracts:    on the primary 'mac' (this machine is a sync satellite)" in out
    assert f"primary run: 24h ago — {stamp}, as of the last pull 2026-10-28T13:24:54Z" in out


def test_status_on_a_satellite_with_nothing_pulled_says_so(satellite, capsys):
    status_cmd._print_auto_brain_status(satellite)
    assert "primary run: none has arrived here (last pull: 2026-10-28T13:24:54Z)" \
        in capsys.readouterr().out


# --- withheld is not waiting -------------------------------------------------


def test_withheld_changes_are_named_apart_from_the_files_waiting_to_push(capsys):
    summary = {"ahead": 0, "behind": 0, "pending": 0, "withheld": 92}
    assert sync_rows._row_commits(summary, {}) is True
    out = capsys.readouterr().out
    assert ("0 files waiting to push, 92 withheld (changes a satellite may not publish; "
            "they stay on this machine)") in out
    sync_rows._row_commits(dict(summary, withheld=0, pending=2), {})
    assert "withheld" not in capsys.readouterr().out


# --- the caps ----------------------------------------------------------------


@pytest.fixture
def lost_rows(monkeypatch):
    rows = [{"short_id": f"child{n:03d}", "parent": "parent00", "pid": 100 + n, "at": 0}
            for n in range(_lists.CAP + 3)]
    monkeypatch.setattr("mnemo.core.sessions.report_card.lost", lambda vault: rows)
    return rows


def test_child_report_rows_stop_at_the_cap_and_say_how_many_more(tmp_path, lost_rows, capsys):
    assert child_reports._doctor_check_child_reports(tmp_path) is False
    out = capsys.readouterr().out
    assert f"{len(lost_rows)} reporter(s)" in out
    for row in lost_rows[:_lists.CAP]:
        assert row["short_id"] in out
    assert lost_rows[_lists.CAP]["short_id"] not in out
    assert "… 3 more — `mnemo doctor --all` lists them" in out


def test_all_prints_every_row(tmp_path, lost_rows, capsys):
    _lists.show_all(True)
    try:
        child_reports._doctor_check_child_reports(tmp_path)
    finally:
        _lists.show_all(False)
    out = capsys.readouterr().out
    for row in lost_rows:
        assert row["short_id"] in out
    assert "more —" not in out


def test_a_list_at_the_cap_is_whole():
    assert _lists.capped(list(range(_lists.CAP))) == (list(range(_lists.CAP)), 0)
    assert _lists.capped(list(range(_lists.CAP + 1))) == (list(range(_lists.CAP)), 1)


def test_doctor_takes_all_and_resets_it_after_the_run(monkeypatch, tmp_path):
    from mnemo.cli.commands import doctor
    from mnemo.cli.parser import _build_parser as build_parser

    args = build_parser().parse_args(["doctor", "--all"])
    assert args.all is True
    assert build_parser().parse_args(["doctor"]).all is False

    seen = []
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: tmp_path)
    monkeypatch.setattr(doctor, "_detect_install_scope", lambda: None)
    monkeypatch.setattr("mnemo.install.preflight.run_preflight",
                        lambda **k: type("R", (), {"issues": [], "ok": True})())
    monkeypatch.setattr(doctor, "DOCTOR_CHECKS", [("probe", lambda v: seen.append(_lists._all))])
    monkeypatch.setattr(doctor, "_doctor_report_recall", lambda v: None)
    assert doctor.cmd_doctor(args) == 0
    assert seen == [True] and _lists._all is False
