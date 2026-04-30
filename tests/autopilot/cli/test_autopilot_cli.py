import json
from pathlib import Path

import pytest

from mnemo.cli.runtime import main


def _run(monkeypatch, tmp_path: Path, *args: str, capsys) -> tuple[int, str]:
    # mnemo resolves vault from cfg; point everything at tmp_path
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "mnemo.cli._resolve_vault", lambda: tmp_path, raising=False
    )
    rc = main(["mnemo", *args])
    out, _err = capsys.readouterr()
    return rc, out


def test_autopilot_status_default_off(monkeypatch, tmp_path, capsys):
    rc, out = _run(monkeypatch, tmp_path, "autopilot", "status", capsys=capsys)
    assert rc == 0
    assert "off" in out.lower()


def test_autopilot_on_then_status_shows_on(monkeypatch, tmp_path, capsys):
    rc, _ = _run(monkeypatch, tmp_path, "autopilot", "on", capsys=capsys)
    assert rc == 0
    rc, out = _run(monkeypatch, tmp_path, "autopilot", "status", capsys=capsys)
    assert "on" in out.lower()


def test_autopilot_off(monkeypatch, tmp_path, capsys):
    _run(monkeypatch, tmp_path, "autopilot", "on", capsys=capsys)
    rc, _ = _run(monkeypatch, tmp_path, "autopilot", "off", capsys=capsys)
    assert rc == 0
    rc, out = _run(monkeypatch, tmp_path, "autopilot", "status", capsys=capsys)
    assert "off" in out.lower()


def test_autopilot_pause_with_hours(monkeypatch, tmp_path, capsys):
    _run(monkeypatch, tmp_path, "autopilot", "on", capsys=capsys)
    rc, _ = _run(monkeypatch, tmp_path, "autopilot", "pause", "--hours", "2",
                 capsys=capsys)
    assert rc == 0
    state = json.loads((tmp_path / ".mnemo" / "autopilot.json").read_text())
    assert state["state"] == "paused"
    assert state["paused_until"] is not None


def test_autopilot_freezes_recall_on_on_when_present(
    monkeypatch, tmp_path, capsys
):
    (tmp_path / ".mnemo").mkdir()
    (tmp_path / ".mnemo" / "recall-cases.json").write_text('{"v":1}')
    _run(monkeypatch, tmp_path, "autopilot", "on", capsys=capsys)
    assert (tmp_path / ".mnemo" / "recall-cases.frozen.json").exists()
