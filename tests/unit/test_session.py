# tests/unit/test_session.py
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from mnemo.core import session


def test_save_then_load_roundtrip(tmp_tempdir: Path):
    info = {"agent": "foo", "repo_root": "/x", "has_git": True}
    session.save("abc123", info)
    loaded = session.load("abc123")
    assert loaded == info


def test_load_missing_returns_none(tmp_tempdir: Path):
    assert session.load("never-existed") is None


def test_load_corrupted_returns_none_and_deletes(tmp_tempdir: Path):
    session.save("xyz", {"a": 1})
    cache_file = session._cache_file("xyz")
    cache_file.write_text("{not valid json")
    assert session.load("xyz") is None
    assert not cache_file.exists()


def test_clear_removes_file(tmp_tempdir: Path):
    session.save("toclear", {"a": 1})
    session.clear("toclear")
    assert session.load("toclear") is None


def test_cleanup_stale_removes_old_entries(tmp_tempdir: Path):
    session.save("old", {"a": 1})
    session.save("fresh", {"b": 2})
    old_file = session._cache_file("old")
    ancient = time.time() - 100_000  # >24h
    os.utime(old_file, (ancient, ancient))
    session.cleanup_stale(max_age_seconds=86400)
    assert session.load("old") is None
    assert session.load("fresh") == {"b": 2}


def test_cache_dir_under_tempdir(tmp_tempdir: Path):
    assert session._cache_dir().parent == tmp_tempdir or str(session._cache_dir()).startswith(str(tmp_tempdir))
