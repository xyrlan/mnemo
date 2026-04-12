# tests/e2e/test_full_session_cycle.py
"""Critical test from spec § 10.3: full SessionStart → prompts → edits → SessionEnd."""
from __future__ import annotations

import io
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from mnemo import cli
from mnemo.hooks import session_start, session_end, user_prompt, post_tool_use


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

    # 4. Three prompts
    for prompt in ["add validation", "fix the bug", "write tests"]:
        _stdin(monkeypatch, {"session_id": "S1", "prompt": prompt})
        assert user_prompt.main() == 0

    # 5. Two file edits
    _stdin(monkeypatch, {
        "session_id": "S1",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(src)},
        "tool_response": {"filePath": str(src)},
    })
    assert post_tool_use.main() == 0
    new_file = repo / "src" / "validate.py"
    _stdin(monkeypatch, {
        "session_id": "S1",
        "tool_name": "Write",
        "tool_input": {"file_path": str(new_file)},
        "tool_response": {"filePath": str(new_file)},
    })
    assert post_tool_use.main() == 0

    # 6. SessionEnd
    _stdin(monkeypatch, {"session_id": "S1", "reason": "exit"})
    assert session_end.main() == 0

    # 7. Verify the daily log
    log = (tmp_home / "vault" / "bots" / "repo" / "logs" / f"{date.today().isoformat()}.md").read_text()
    assert "🟢 session started (startup)" in log
    assert "💬 add validation" in log
    assert "💬 fix the bug" in log
    assert "💬 write tests" in log
    assert "✏️ edited `src/main.py`" in log
    assert "✏️ created `src/validate.py`" in log
    assert "🔴 session ended (exit)" in log

    # 8. Cache is cleared
    from mnemo.core import session as sess
    assert sess.load("S1") is None
