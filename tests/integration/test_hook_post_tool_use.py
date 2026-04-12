# tests/integration/test_hook_post_tool_use.py
from __future__ import annotations

import io
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from mnemo.core import session
from mnemo.hooks import post_tool_use


@pytest.fixture
def hook_env(tmp_vault: Path, tmp_home: Path, tmp_tempdir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    return tmp_vault


def test_logs_edit_with_relative_path(hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)
    src_file = repo / "src" / "x.py"
    src_file.parent.mkdir(parents=True)
    src_file.write_text("x")
    session.save("S", {"name": "myrepo", "repo_root": str(repo), "has_git": True})
    payload = json.dumps({
        "session_id": "S",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(src_file)},
        "tool_response": {"filePath": str(src_file), "success": True},
    })
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert post_tool_use.main() == 0
    log = (hook_env / "bots" / "myrepo" / "logs" / f"{date.today().isoformat()}.md").read_text()
    assert "✏️ edited `src/x.py`" in log


def test_logs_write_as_created(hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "r"
    (repo / ".git").mkdir(parents=True)
    f = repo / "new.py"
    session.save("S", {"name": "r", "repo_root": str(repo), "has_git": True})
    payload = json.dumps({
        "session_id": "S",
        "tool_name": "Write",
        "tool_input": {"file_path": str(f)},
        "tool_response": {"filePath": str(f)},
    })
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    post_tool_use.main()
    log = (hook_env / "bots" / "r" / "logs" / f"{date.today().isoformat()}.md").read_text()
    assert "✏️ created `new.py`" in log


def test_uses_basename_when_outside_repo(hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "r"
    (repo / ".git").mkdir(parents=True)
    outside = tmp_path / "elsewhere" / "stray.md"
    outside.parent.mkdir()
    session.save("S", {"name": "r", "repo_root": str(repo), "has_git": True})
    payload = json.dumps({
        "session_id": "S",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(outside)},
        "tool_response": {"filePath": str(outside)},
    })
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    post_tool_use.main()
    log = (hook_env / "bots" / "r" / "logs" / f"{date.today().isoformat()}.md").read_text()
    assert "stray.md" in log


def test_skips_when_file_path_missing(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    session.save("S", {"name": "r", "repo_root": "/x", "has_git": False})
    payload = json.dumps({"session_id": "S", "tool_name": "Edit", "tool_input": {}, "tool_response": {}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert post_tool_use.main() == 0
    log_path = hook_env / "bots" / "r" / "logs" / f"{date.today().isoformat()}.md"
    assert not log_path.exists()


def test_respects_capture_flag(hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg_path = hook_env / "mnemo.config.json"
    cfg_path.write_text(json.dumps({
        "vaultRoot": str(hook_env),
        "capture": {"fileEdits": False},
    }))
    repo = tmp_path / "r"
    (repo / ".git").mkdir(parents=True)
    f = repo / "x.py"
    session.save("S", {"name": "r", "repo_root": str(repo), "has_git": True})
    payload = json.dumps({
        "session_id": "S",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(f)},
        "tool_response": {"filePath": str(f)},
    })
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    post_tool_use.main()
    log_path = hook_env / "bots" / "r" / "logs" / f"{date.today().isoformat()}.md"
    assert not log_path.exists()
