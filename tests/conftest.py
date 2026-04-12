"""Shared pytest fixtures for mnemo tests."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Path:
    """Create a minimal vault directory tree and return its root."""
    root = tmp_path / "vault"
    (root / "bots").mkdir(parents=True)
    (root / "shared").mkdir()
    (root / "wiki" / "sources").mkdir(parents=True)
    (root / "wiki" / "compiled").mkdir()
    (root / "mnemo.config.json").write_text(json.dumps({"vaultRoot": str(root)}))
    return root


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect HOME to a temp dir so ~/.claude and ~/mnemo are isolated."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows compatibility
    return home


@pytest.fixture
def tmp_tempdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect tempfile.gettempdir() to an isolated dir.

    Sets env vars AND patches tempfile.tempdir directly — CPython caches
    gettempdir() on first call, so the env vars alone would not affect an
    already-running session.
    """
    td = tmp_path / "tmp"
    td.mkdir()
    monkeypatch.setenv("TMPDIR", str(td))
    monkeypatch.setenv("TEMP", str(td))
    monkeypatch.setenv("TMP", str(td))
    monkeypatch.setattr(tempfile, "tempdir", str(td))
    return td
