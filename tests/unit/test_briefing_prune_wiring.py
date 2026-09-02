"""Wiring for briefing retention (#116): session_start marker, CLI flags, status line."""
from __future__ import annotations

import io
import json
import os
import time

from mnemo.hooks import session_start


def _setup(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = tmp_path / "mnemo.config.json"
    cfg.write_text(
        json.dumps({"vaultRoot": str(vault),
                    "briefings": {"retentionDays": 30, "keepPerAgent": 0}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(cfg))
    p = vault / "bots" / "a" / "briefings" / "sessions" / "old.md"
    p.parent.mkdir(parents=True)
    p.write_text("x", encoding="utf-8")
    t = time.time() - 90 * 86400
    os.utime(p, (t, t))
    return vault, p


def _hook(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"session_id": "s", "cwd": str(tmp_path)}))
    )
    return session_start.main()


def test_session_start_prunes_once_per_week(tmp_path, monkeypatch):
    vault, old = _setup(tmp_path, monkeypatch)
    assert _hook(tmp_path, monkeypatch) == 0
    assert not old.exists()
    marker = vault / ".mnemo" / "briefings-prune.last"
    assert marker.exists()
    # second run within 7 days: a fresh old briefing survives
    old.write_text("x", encoding="utf-8")
    t = time.time() - 90 * 86400
    os.utime(old, (t, t))
    _hook(tmp_path, monkeypatch)
    assert old.exists()


def test_briefing_cli_prune_dry_run(tmp_path, monkeypatch, capsys):
    vault, old = _setup(tmp_path, monkeypatch)
    from mnemo.cli import main as cli_main
    assert cli_main(["briefing", "--prune", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would delete 1" in out and old.exists()
    assert cli_main(["briefing", "--prune"]) == 0
    assert not old.exists()


def test_briefing_cli_without_prune_or_positionals_is_a_usage_error(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch)
    from mnemo.cli import main as cli_main
    assert cli_main(["briefing"]) == 2
    assert "usage: mnemo briefing" in capsys.readouterr().err


def _status_out(tmp_path, monkeypatch, capsys, *, retention_days=None):
    vault, old = _setup(tmp_path, monkeypatch)
    cfg_path = tmp_path / "mnemo.config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    # Status reports on the *defaults* unless a test overrides them.
    cfg["briefings"] = {} if retention_days is None else {"retentionDays": retention_days}
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    from mnemo.cli import main as cli_main
    assert cli_main(["status"]) == 0
    return capsys.readouterr().out, old


def test_status_reports_briefings_with_policy(tmp_path, monkeypatch, capsys):
    out, old = _status_out(tmp_path, monkeypatch, capsys)
    assert "Briefings: 1 across 1 agents (0.0 MB) — 0 prunable (retention 180d, keep 20/agent)" in out
    assert old.exists()  # status is a dry run


def test_status_says_retention_off_when_disabled(tmp_path, monkeypatch, capsys):
    out, _ = _status_out(tmp_path, monkeypatch, capsys, retention_days=0)
    assert "0 prunable (retention off)" in out


def test_status_skips_briefings_line_when_none(tmp_path, monkeypatch, capsys):
    vault, old = _setup(tmp_path, monkeypatch)
    old.unlink()
    from mnemo.cli import main as cli_main
    assert cli_main(["status"]) == 0
    assert "Briefings:" not in capsys.readouterr().out
