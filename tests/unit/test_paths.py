from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from mnemo.core import paths


def test_vault_root_expands_tilde(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = {"vaultRoot": "~/mnemo"}
    assert paths.vault_root(cfg) == tmp_path / "mnemo"


def test_vault_root_absolute(tmp_path: Path):
    cfg = {"vaultRoot": str(tmp_path / "explicit")}
    assert paths.vault_root(cfg) == tmp_path / "explicit"


def test_logs_dir_for_agent(tmp_path: Path):
    cfg = {"vaultRoot": str(tmp_path)}
    assert paths.logs_dir(cfg, "myrepo") == tmp_path / "bots" / "myrepo" / "logs"


def test_memory_dir_for_agent(tmp_path: Path):
    cfg = {"vaultRoot": str(tmp_path)}
    assert paths.memory_dir(cfg, "myrepo") == tmp_path / "bots" / "myrepo" / "memory"


def test_today_log_path(tmp_path: Path):
    cfg = {"vaultRoot": str(tmp_path)}
    today = date.today().isoformat()
    expected = tmp_path / "bots" / "myrepo" / "logs" / f"{today}.md"
    assert paths.today_log(cfg, "myrepo") == expected


def test_errors_log_path(tmp_path: Path):
    cfg = {"vaultRoot": str(tmp_path)}
    assert paths.errors_log(cfg) == tmp_path / ".errors.log"


def test_ensure_writeable_creates_dir(tmp_path: Path):
    target = tmp_path / "newdir"
    paths.ensure_writeable(target)
    assert target.exists() and target.is_dir()
