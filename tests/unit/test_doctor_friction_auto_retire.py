"""#553: ``friction.autoRetire: true`` has nothing behind it, and doctor says so."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import mnemo
from mnemo.cli.commands import doctor
from mnemo.cli.commands.doctor_checks import misc


@pytest.fixture
def config(monkeypatch):
    def use(cfg):
        monkeypatch.setattr("mnemo.core.config.load_config", lambda *a, **k: cfg)
    return use


def test_doctor_names_the_key_inert_when_it_is_on(tmp_path, config, capsys):
    config({"friction": {"autoRetire": True}})
    assert misc._doctor_check_friction_auto_retire(tmp_path) is False
    out = capsys.readouterr().out
    assert "friction.autoRetire is true, but it is inert" in out


@pytest.mark.parametrize("cfg", [{}, {"friction": {}}, {"friction": {"autoRetire": False}},
                                 {"friction": {"autoRetire": "yes"}}])
def test_doctor_is_silent_when_the_key_is_off(tmp_path, config, capsys, cfg):
    config(cfg)
    assert misc._doctor_check_friction_auto_retire(tmp_path) is True
    assert capsys.readouterr().out == ""


def test_the_check_is_registered():
    assert ("friction_auto_retire", misc._doctor_check_friction_auto_retire) in doctor.DOCTOR_CHECKS


def test_nothing_calls_auto_retire_yet():
    """The pin behind the message: the day something calls ``auto_retire``,
    the key is no longer inert and this check has to go with it."""
    src = Path(mnemo.__file__).parent
    callers = [
        str(p.relative_to(src)) for p in src.rglob("*.py")
        if p.name != "retire.py" and re.search(r"\bauto_retire\(", p.read_text(encoding="utf-8"))
    ]
    assert callers == []
