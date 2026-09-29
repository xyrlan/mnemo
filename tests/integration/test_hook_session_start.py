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
    log = (hook_env / "bots" / "myrepo" / "logs" / f"{date.today().isoformat()}.md").read_text(encoding="utf-8")
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
    }), encoding="utf-8")
    repo = tmp_path / "r2"
    (repo / ".git").mkdir(parents=True)
    payload = json.dumps({"session_id": "S2", "cwd": str(repo), "source": "resume"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    rc = session_start.main()
    assert rc == 0
    log_dir = hook_env / "bots" / "r2" / "logs"
    assert not log_dir.exists() or not any(log_dir.iterdir())


def test_session_start_records_its_inbox_without_the_messaging_token(
    hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """#553: the address is written, and the token Claude Code exports beside
    the socket lands nowhere mnemo writes — not the address map, not a log,
    ledger or telemetry row. Searched by value, across the whole run's tree."""
    from mnemo.core.sessions import inbox

    secret = "f00dfacecafe0123456789abcdef5553"
    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "S553")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", "/tmp/cc-socks/4242.sock")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_TOKEN", secret)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "session_id": "S553", "cwd": str(repo), "source": "startup",
    })))

    assert session_start.main() == 0

    row = inbox.lookup(hook_env, "S553")
    assert row is not None and row["socket"] == "/tmp/cc-socks/4242.sock"
    hits = []
    for path in tmp_path.rglob("*"):
        try:
            if path.is_file() and secret.encode() in path.read_bytes():
                hits.append(str(path.relative_to(tmp_path)))
        except OSError:
            continue
    assert hits == []
