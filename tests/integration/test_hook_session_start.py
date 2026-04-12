# tests/integration/test_hook_session_start.py
from __future__ import annotations

import io
import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

from mnemo.hooks import session_start
from mnemo.core import session


@pytest.fixture
def hook_env(tmp_vault: Path, tmp_home: Path, tmp_tempdir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    return tmp_vault


def test_session_start_writes_log_and_caches_session(hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)
    payload = json.dumps({
        "session_id": "S1",
        "cwd": str(repo),
        "source": "startup",
    })
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    rc = session_start.main()
    assert rc == 0
    cached = session.load("S1")
    assert cached is not None
    assert cached["name"] == "myrepo"
    log = (hook_env / "bots" / "myrepo" / "logs" / f"{date.today().isoformat()}.md").read_text()
    assert "🟢 session started (startup)" in log


def test_session_start_swallows_malformed_payload(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not valid"))
    rc = session_start.main()
    assert rc == 0  # never crash


def test_session_start_respects_disabled_capture(hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg_path = hook_env / "mnemo.config.json"
    cfg_path.write_text(json.dumps({
        "vaultRoot": str(hook_env),
        "capture": {"sessionStartEnd": False},
    }))
    repo = tmp_path / "r2"
    (repo / ".git").mkdir(parents=True)
    payload = json.dumps({"session_id": "S2", "cwd": str(repo), "source": "resume"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    rc = session_start.main()
    assert rc == 0
    log_dir = hook_env / "bots" / "r2" / "logs"
    assert not log_dir.exists() or not any(log_dir.iterdir())
