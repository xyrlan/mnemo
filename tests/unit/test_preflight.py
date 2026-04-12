from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from mnemo.install import preflight


def test_clean_env_passes(tmp_home: Path):
    result = preflight.run_preflight(vault_root=tmp_home / "mnemo")
    assert result.ok is True
    assert all(i.severity != "error" for i in result.issues)


def test_python_version_check(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(preflight, "_python_ok", lambda: False)
    result = preflight.run_preflight(vault_root=tmp_home / "mnemo")
    assert result.ok is False
    assert any(i.kind == "python_version" for i in result.issues)


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX chmod semantics not honored on Windows")
def test_unwritable_vault_parent(tmp_path: Path):
    parent = tmp_path / "ro"
    parent.mkdir()
    parent.chmod(0o500)  # read+exec only
    try:
        result = preflight.run_preflight(vault_root=parent / "mnemo")
        assert result.ok is False
        assert any(i.kind == "vault_unwritable" for i in result.issues)
    finally:
        parent.chmod(0o700)


def test_missing_rsync_is_warning_not_error(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = preflight.run_preflight(vault_root=tmp_home / "mnemo")
    assert result.ok is True  # warning only
    assert any(i.kind == "rsync_missing" and i.severity == "warning" for i in result.issues)


def test_issue_has_remediation(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(preflight, "_python_ok", lambda: False)
    result = preflight.run_preflight(vault_root=tmp_home / "mnemo")
    issues = [i for i in result.issues if i.kind == "python_version"]
    assert issues and issues[0].remediation
