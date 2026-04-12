# tests/integration/test_hook_user_prompt.py
from __future__ import annotations

import io
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from mnemo.core import session
from mnemo.hooks import user_prompt


@pytest.fixture
def hook_env(tmp_vault: Path, tmp_home: Path, tmp_tempdir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    return tmp_vault


def test_logs_first_line_of_prompt(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    session.save("S", {"name": "repo", "repo_root": "/x", "has_git": True})
    payload = json.dumps({"session_id": "S", "prompt": "add validation\nto the form"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert user_prompt.main() == 0
    log = (hook_env / "bots" / "repo" / "logs" / f"{date.today().isoformat()}.md").read_text()
    assert "💬 add validation" in log
    assert "to the form" not in log  # only first line


def test_truncates_long_prompts(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    session.save("S", {"name": "repo", "repo_root": "/x", "has_git": True})
    huge = "x" * 500
    payload = json.dumps({"session_id": "S", "prompt": huge})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    user_prompt.main()
    log = (hook_env / "bots" / "repo" / "logs" / f"{date.today().isoformat()}.md").read_text()
    line = [l for l in log.splitlines() if "💬" in l][0]
    # First-line truncation cap is 200 chars in spec
    assert len(line) < 350  # accounting for prefix


def test_skips_empty_prompt(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    session.save("S", {"name": "repo", "repo_root": "/x", "has_git": True})
    payload = json.dumps({"session_id": "S", "prompt": "   \n\n"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    user_prompt.main()
    log_path = hook_env / "bots" / "repo" / "logs" / f"{date.today().isoformat()}.md"
    assert not log_path.exists()


def test_skips_system_reminder(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    session.save("S", {"name": "repo", "repo_root": "/x", "has_git": True})
    payload = json.dumps({"session_id": "S", "prompt": "<system-reminder>x</system-reminder>"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    user_prompt.main()
    log_path = hook_env / "bots" / "repo" / "logs" / f"{date.today().isoformat()}.md"
    assert not log_path.exists()


def test_escapes_backticks(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    session.save("S", {"name": "repo", "repo_root": "/x", "has_git": True})
    payload = json.dumps({"session_id": "S", "prompt": "fix `bad` thing"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    user_prompt.main()
    log = (hook_env / "bots" / "repo" / "logs" / f"{date.today().isoformat()}.md").read_text()
    assert "fix" in log and "bad" in log
    # backticks neutralized to single quotes (chosen escape)
    assert "`" not in [l for l in log.splitlines() if "💬" in l][0]


def test_respects_capture_flag(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    cfg_path = hook_env / "mnemo.config.json"
    cfg_path.write_text(json.dumps({
        "vaultRoot": str(hook_env),
        "capture": {"userPrompt": False},
    }))
    session.save("S", {"name": "repo", "repo_root": "/x", "has_git": True})
    payload = json.dumps({"session_id": "S", "prompt": "anything"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    user_prompt.main()
    log_path = hook_env / "bots" / "repo" / "logs" / f"{date.today().isoformat()}.md"
    assert not log_path.exists()
