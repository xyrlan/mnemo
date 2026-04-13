"""Integration test for the hint emitter inside the SessionEnd hook."""
from __future__ import annotations

import io
import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from mnemo.hooks import session_end


def _write_state(vault_root: Path) -> None:
    d = vault_root / ".mnemo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "extraction-state.json").write_text(json.dumps({
        "schema_version": 1,
        "last_run": "2026-04-01T00:00:00",
        "entries": {},
    }))


def _touch(vault_root: Path, agent: str, n: int) -> None:
    d = vault_root / "bots" / agent / "memory"
    d.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for i in range(n):
        p = d / f"f{i}.md"
        p.write_text(f"---\ntype: feedback\n---\nbody")
        import os
        os.utime(p, (now, now))


def test_session_end_hook_emits_hint_end_to_end(tmp_vault, tmp_home, monkeypatch):
    # Point mnemo at the tmp vault
    cfg_path = tmp_vault / "mnemo.config.json"
    cfg_path.write_text(json.dumps({"vaultRoot": str(tmp_vault)}))
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(cfg_path))

    _write_state(tmp_vault)
    _touch(tmp_vault, "my-agent", 6)

    payload = {
        "session_id": "abc123",
        "cwd": str(tmp_vault),
        "reason": "exit",
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    rc = session_end.main()
    assert rc == 0

    # Find any log file that has the hint (agent-name resolution from cwd may pick
    # whichever name — the key assertion is that SOME log has the hint).
    log_files = list((tmp_vault / "bots").glob("*/logs/*.md"))
    assert any("🟡" in f.read_text() for f in log_files), f"no hint found in {log_files}"


def test_session_end_hook_silent_when_no_state(tmp_vault, tmp_home, monkeypatch):
    cfg_path = tmp_vault / "mnemo.config.json"
    cfg_path.write_text(json.dumps({"vaultRoot": str(tmp_vault)}))
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(cfg_path))

    _touch(tmp_vault, "my-agent", 10)  # many new files, but no state file

    payload = {"session_id": "abc", "cwd": str(tmp_vault), "reason": "exit"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    rc = session_end.main()
    assert rc == 0
    log_files = list((tmp_vault / "bots").glob("*/logs/*.md"))
    assert not any("🟡" in f.read_text() for f in log_files if f.exists())
