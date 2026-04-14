# tests/e2e/test_full_session_cycle.py
"""Critical test from spec § 10.3: full SessionStart → SessionEnd lifecycle.

v0.3.1 removed the user_prompt and post_tool_use hooks (they were write-only
log amplifiers with no downstream consumers). The remaining lifecycle is
SessionStart → SessionEnd; this test verifies the two-hook cycle still
produces a usable daily log with green/red markers and clears the session
IPC cache on exit.
"""
from __future__ import annotations

import io
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from mnemo import cli
from mnemo.hooks import session_start, session_end


def _stdin(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))


def test_full_session_cycle(tmp_home: Path, tmp_tempdir: Path, monkeypatch: pytest.MonkeyPatch):
    # 1. Install
    rc = cli.main(["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"])
    assert rc == 0

    # 2. Set up a fake repo
    repo = tmp_home / "repo"
    (repo / ".git").mkdir(parents=True)
    src = repo / "src" / "main.py"
    src.parent.mkdir(parents=True)
    src.write_text("print('hi')")

    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_home / "vault" / "mnemo.config.json"))

    # 3. SessionStart
    _stdin(monkeypatch, {"session_id": "S1", "cwd": str(repo), "source": "startup"})
    assert session_start.main() == 0

    # 4. SessionEnd
    _stdin(monkeypatch, {"session_id": "S1", "reason": "exit"})
    assert session_end.main() == 0

    # 5. Verify the daily log has green/red markers (the only log writes remaining)
    log = (tmp_home / "vault" / "bots" / "repo" / "logs" / f"{date.today().isoformat()}.md").read_text()
    assert "🟢 session started (startup)" in log
    assert "🔴 session ended (exit)" in log

    # 6. Session IPC cache is cleared on session end
    from mnemo.core import session as sess
    assert sess.load("S1") is None
