"""``report_card.lost`` and its doctor check (#460): a reporter that died
without a row is named, not left for the maintainer to notice."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mnemo.cli.commands.doctor_checks.child_reports import _doctor_check_child_reports
from mnemo.core.sessions import report_card as rc

T0 = datetime(2026, 9, 23, 10, 50, tzinfo=timezone(timedelta(hours=-3)))
NOW = (T0 + timedelta(hours=1)).timestamp()


def _log(vault: Path, *rows) -> None:
    path = vault / ".mnemo" / rc.LOG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for minutes, row in rows:
            ts = (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S%z")
            fh.write(json.dumps({"ts": ts, **row}) + "\n")


def _spawned(sid, pid):
    return {"short_id": sid, "parent": "P1", "event": "spawned", "pid": pid}


def _lost(vault, **kw):
    return [(r["short_id"], r["pid"]) for r in rc.lost(vault, now=NOW, **kw)]


def test_the_2026_09_23_log_names_the_silent_reporter(tmp_path) -> None:
    """The issue's own log: 9b02c808 reported and settled; a62d823c did not."""
    _log(
        tmp_path,
        (0, _spawned("9b02c808", 3768)),
        (0, {"short_id": "9b02c808", "parent": "P1", "event": "finished", "delivered": True}),
        (3, _spawned("a62d823c", 14325)),
        (9, {"short_id": "9b02c808", "parent": "P1", "event": "settled", "delivered": True}),
    )

    assert _lost(tmp_path) == [("a62d823c", 14325)]


def test_a_second_spawn_is_not_answered_by_the_first_ones_row(tmp_path) -> None:
    """22bcd72f on 2026-09-23: reported at 01:47, respawned, silent at 01:54."""
    _log(
        tmp_path,
        (0, _spawned("22bcd72f", 1)),
        (0, {"short_id": "22bcd72f", "parent": "P1", "event": "finished"}),
        (7, _spawned("22bcd72f", 2)),
    )

    assert _lost(tmp_path) == [("22bcd72f", 2)]


def test_a_stamped_reporter_pairs_by_pid_even_when_it_lands_first(tmp_path) -> None:
    _log(
        tmp_path,
        (0, {"short_id": "c0da0f55", "parent": "P1", "event": "finished", "reporter": 7}),
        (0, _spawned("c0da0f55", 7)),
        (5, _spawned("c0da0f55", 8)),
        # Hook-written, no reporter: after #460 it answers nothing.
        (6, {"short_id": "c0da0f55", "parent": "P1", "event": "finished", "delivered": False}),
    )

    assert _lost(tmp_path) == [("c0da0f55", 8)]


def _finished(sid, reporter, **kw):
    return {"short_id": sid, "parent": "P1", "event": "finished", "reporter": reporter, **kw}


def test_a_windows_reporter_whose_pid_is_not_the_hooks_is_not_lost(tmp_path) -> None:
    """#755, 917b2535 on 2026-10-20: the hook got the venv launcher's pid."""
    _log(
        tmp_path,
        (0, _spawned("917b2535", 36728)),
        (0, _finished("917b2535", 37740, state="ready", pr=509, delivered=True)),
        # Its reporter can land first, too.
        (2, _finished("a1b2c3d4", 41000)),
        (2, _spawned("a1b2c3d4", 40996)),
    )

    assert _lost(tmp_path) == []


def test_a_windows_reporter_that_wrote_nothing_is_still_lost(tmp_path) -> None:
    _log(
        tmp_path,
        (0, _spawned("917b2535", 36728)),
        (0, _finished("917b2535", 37740)),
        (5, _spawned("917b2535", 36900)),
        # Another child's reporter answers nothing for this one.
        (6, _finished("0badc0de", 37744)),
        (6, _spawned("0badc0de", 37700)),
    )

    assert _lost(tmp_path) == [("917b2535", 36900)]


def test_a_reused_pid_does_not_pair_another_childs_spawn(tmp_path) -> None:
    """Windows hands pids out again within days, to any child's reporter."""
    _log(
        tmp_path,
        (0, _spawned("b0094cb2", 69184)),
        (0, _finished("b0094cb2", 59744)),
        (9, _spawned("c503765d", 59744)),
    )

    assert _lost(tmp_path) == [("c503765d", 59744)]


def test_a_spawn_and_its_reporter_pair_by_run(tmp_path) -> None:
    _log(
        tmp_path,
        (0, _finished("c0da0f55", 7, run="r1")),
        (0, {**_spawned("c0da0f55", 3), "run": "r1"}),
        # A second reporter of the same child, running for r1's PR watch: it
        # answers no other run.
        (5, {**_spawned("c0da0f55", 4), "run": "r2"}),
        (6, _finished("c0da0f55", 8, run="r9")),
    )

    assert _lost(tmp_path) == [("c0da0f55", 4)]


def test_too_young_or_too_old_is_not_flagged(tmp_path) -> None:
    _log(tmp_path, (55, _spawned("young000", 1)), (-60 * 24 * 8, _spawned("old00000", 2)))

    assert _lost(tmp_path) == []
    assert _lost(tmp_path, window_days=30) == [("old00000", 2)]


def test_a_missing_or_garbled_log_is_silent(tmp_path) -> None:
    assert rc.lost(tmp_path) == []
    (tmp_path / ".mnemo").mkdir()
    (tmp_path / ".mnemo" / rc.LOG_NAME).write_text('not json\n{"ts": 3}\n[]\n', encoding="utf-8")
    assert rc.lost(tmp_path) == []


def test_doctor_warns_with_the_child_and_its_parent(tmp_path, monkeypatch, capsys) -> None:
    _log(tmp_path, (3, {**_spawned("a62d823c", 14325), "parent": "76e8f2ad-e2c2"}))
    real = rc.lost
    monkeypatch.setattr(rc, "lost", lambda v, **k: real(v, now=NOW))

    assert _doctor_check_child_reports(tmp_path) is False
    out = capsys.readouterr().out
    assert "1 reporter(s)" in out and "a62d823c -> 76e8f2ad" in out and "pid 14325" in out


def test_doctor_is_silent_without_a_lost_report(tmp_path) -> None:
    assert _doctor_check_child_reports(tmp_path) is True


def test_the_check_is_registered() -> None:
    from mnemo.cli.commands.doctor import DOCTOR_CHECKS

    assert ("child_reports", _doctor_check_child_reports) in DOCTOR_CHECKS
