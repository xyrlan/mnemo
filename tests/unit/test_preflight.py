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


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX chmod semantics not honored on Windows")
@pytest.mark.skipif(os.geteuid() == 0 if hasattr(os, "geteuid") else False, reason="root ignores file modes")
def test_check_settings_false_skips_the_settings_probe(tmp_home: Path):
    """Hosts that never write ~/.claude/settings.json must not be blocked by it."""
    settings = tmp_home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{}")
    settings.chmod(0o444)
    try:
        blocked = preflight.run_preflight(vault_root=tmp_home / "mnemo")
        assert blocked.ok is False
        assert any(i.kind == "settings_unwritable" for i in blocked.issues)

        skipped = preflight.run_preflight(vault_root=tmp_home / "mnemo", check_settings=False)
        assert skipped.ok is True
        assert not any(i.kind == "settings_unwritable" for i in skipped.issues)
    finally:
        settings.chmod(0o600)
