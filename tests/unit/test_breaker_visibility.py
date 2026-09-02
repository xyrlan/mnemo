"""An open circuit breaker is visible from inside a session, status and doctor (#115).

Before this, a tripped breaker made every hook go quiet and mnemo simply
"stopped": status said OPEN with no remedy, doctor said nothing at all.
"""
from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path

import pytest

from mnemo import cli
from mnemo.cli.commands.doctor_checks import misc as doctor_misc
from mnemo.hooks import session_start


def _trip(vault: Path) -> None:
    vault.mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat(timespec="seconds")
    with open(vault / ".errors.log", "w", encoding="utf-8") as fh:
        for _ in range(12):
            fh.write(json.dumps({"timestamp": now, "where": "session_start.injection",
                                 "kind": "BrokenPipeError", "message": "x"}) + "\n")


def _run_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    vault = tmp_path / "vault"
    _trip(vault)
    cfg = tmp_path / "mnemo.config.json"
    cfg.write_text(json.dumps({"vaultRoot": str(vault)}), encoding="utf-8")
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(cfg))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": "s", "cwd": str(tmp_path)})))
    rc = session_start.main()
    return rc, capsys.readouterr().out


def test_session_start_tells_the_user_the_breaker_is_open(tmp_path, monkeypatch, capsys):
    rc, out = _run_hook(tmp_path, monkeypatch, capsys)
    assert rc == 0
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert ctx.startswith("[mnemo] paused: circuit breaker open (12 errors in the last hour, most from session_start.injection)")
    assert "mnemo fix" in ctx


def test_session_start_repeats_the_notice_every_session_while_open(tmp_path, monkeypatch, capsys):
    """No dedupe: the breaker self-heals in an hour and this line is the only signal."""
    rc1, out1 = _run_hook(tmp_path, monkeypatch, capsys)
    rc2, out2 = _run_hook(tmp_path, monkeypatch, capsys)
    assert (rc1, rc2) == (0, 0)
    assert "[mnemo] paused:" in out1 and "[mnemo] paused:" in out2


def test_session_start_does_no_other_work_while_open(tmp_path, monkeypatch, capsys):
    """The notice is emitted before scaffolding: a paused vault stays untouched."""
    _run_hook(tmp_path, monkeypatch, capsys)
    assert not (tmp_path / "vault" / "HOME.md").exists()


def test_doctor_reports_open_breaker(tmp_path, capsys):
    _trip(tmp_path)
    assert doctor_misc._doctor_check_circuit_breaker(tmp_path) is False
    out = capsys.readouterr().out
    assert "circuit breaker open (12 errors" in out and "mnemo fix" in out


def test_doctor_ok_when_closed(tmp_path, capsys):
    assert doctor_misc._doctor_check_circuit_breaker(tmp_path) is True
    assert "closed" in capsys.readouterr().out


def test_doctor_registers_circuit_breaker_first():
    from mnemo.cli.commands import doctor
    assert doctor.DOCTOR_CHECKS[0] == ("circuit_breaker", doctor_misc._doctor_check_circuit_breaker)


def test_status_open_line_names_count_top_and_remedy(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    _trip(vault)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Circuit breaker: OPEN — 12 errors in the last hour (top: session_start.injection ×12). Run `mnemo fix` to reset." in out


def test_status_closed_line_unchanged(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    assert cli.main(["status"]) == 0
    assert "Circuit breaker: closed (ok)" in capsys.readouterr().out
