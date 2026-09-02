from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mnemo.cli.runtime import main


def _run(monkeypatch, tmp_path: Path, *args: str, capsys) -> tuple[int, str]:
    # mnemo resolves vault from cfg; point everything at tmp_path
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "mnemo.cli._resolve_vault", lambda: tmp_path, raising=False
    )
    rc = main([*args])
    out, _err = capsys.readouterr()
    return rc, out


def test_autopilot_status_default_on(monkeypatch, tmp_path, capsys):
    rc, out = _run(monkeypatch, tmp_path, "autopilot", "status", capsys=capsys)
    assert rc == 0
    assert "on" in out.lower()


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


def test_autopilot_status_reports_network_off_by_default(
    monkeypatch, tmp_path, capsys
):
    with patch("mnemo.core.config.load_config", return_value={}):
        rc, out = _run(monkeypatch, tmp_path, "autopilot", "status", capsys=capsys)
    assert rc == 0
    assert (
        "Network: off (autopilot.network.enabled=false — no gh calls; "
        "self-fixes apply in place)"
    ) in out


def test_autopilot_status_reports_network_on_when_enabled(
    monkeypatch, tmp_path, capsys
):
    cfg = {"autopilot": {"network": {"enabled": True}}}
    with patch("mnemo.core.config.load_config", return_value=cfg):
        rc, out = _run(monkeypatch, tmp_path, "autopilot", "status", capsys=capsys)
    assert rc == 0
    assert "Network: on (gh issues/PRs allowed)" in out


def test_autopilot_on_network_off_skips_label_and_prints_hint(
    monkeypatch, tmp_path, capsys
):
    with patch(
        "mnemo.autopilot.core.labels.ensure_label_exists"
    ) as mock_ensure, patch(
        "mnemo.core.config.load_config", return_value={}
    ):
        rc, out = _run(monkeypatch, tmp_path, "autopilot", "on", capsys=capsys)
    assert rc == 0
    mock_ensure.assert_not_called()
    assert "skipped" not in out
    assert "autopilot: on" in out
    assert (
        "network: off — set autopilot.network.enabled=true to let it open "
        "GitHub issues/PRs"
    ) in out


def test_autopilot_on_network_on_calls_ensure_label_and_no_hint(
    monkeypatch, tmp_path, capsys
):
    cfg = {"autopilot": {"network": {"enabled": True}}}
    with patch(
        "mnemo.autopilot.core.labels.ensure_label_exists"
    ) as mock_ensure, patch(
        "mnemo.core.config.load_config", return_value=cfg
    ):
        rc, out = _run(monkeypatch, tmp_path, "autopilot", "on", capsys=capsys)
    assert rc == 0
    mock_ensure.assert_called_once()
    assert "autopilot: on" in out
    assert "network: off" not in out
