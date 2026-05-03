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


import time
from datetime import datetime, timezone
from mnemo.core import session as session_mod


def test_mark_analyzed_stamps_iso_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "_cache_dir", lambda: tmp_path)
    session_mod.save("sid-1", {"name": "proj", "started_at": "2026-05-03T10:00:00"})
    session_mod.mark_analyzed("sid-1")
    loaded = session_mod.load("sid-1")
    assert "analyzed_at" in loaded
    # Parses as ISO-8601
    datetime.fromisoformat(loaded["analyzed_at"].replace("Z", "+00:00"))
    # Other fields preserved
    assert loaded["name"] == "proj"
    assert loaded["started_at"] == "2026-05-03T10:00:00"


def test_mark_analyzed_noop_on_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "_cache_dir", lambda: tmp_path)
    # Should not raise
    session_mod.mark_analyzed("nonexistent-sid")


def test_iter_unanalyzed_returns_recent_unmarked(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "_cache_dir", lambda: tmp_path)
    session_mod.save("sid-fresh", {"name": "p", "started_at": "x", "cwd_at_start": "/a"})
    session_mod.save("sid-marked", {"name": "p", "started_at": "x", "cwd_at_start": "/a"})
    session_mod.mark_analyzed("sid-marked")

    entries = session_mod.iter_unanalyzed(max_age_seconds=26 * 3600)
    sids = {e["session_id"] for e in entries}
    assert sids == {"sid-fresh"}


def test_iter_unanalyzed_filters_by_age(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "_cache_dir", lambda: tmp_path)
    session_mod.save("sid-old", {"name": "p", "started_at": "x", "cwd_at_start": "/a"})
    # Backdate the file's mtime beyond the window
    f = tmp_path / "session-sid-old.json"
    old_ts = time.time() - (48 * 3600)
    import os as _os
    _os.utime(f, (old_ts, old_ts))

    session_mod.save("sid-new", {"name": "p", "started_at": "x", "cwd_at_start": "/a"})

    entries = session_mod.iter_unanalyzed(max_age_seconds=26 * 3600)
    sids = {e["session_id"] for e in entries}
    assert sids == {"sid-new"}


def test_iter_unanalyzed_skips_malformed_files(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "_cache_dir", lambda: tmp_path)
    (tmp_path / "session-broken.json").write_text("not-json{{{")
    session_mod.save("sid-ok", {"name": "p", "started_at": "x", "cwd_at_start": "/a"})

    entries = session_mod.iter_unanalyzed(max_age_seconds=26 * 3600)
    sids = {e["session_id"] for e in entries}
    assert sids == {"sid-ok"}


def test_iter_unanalyzed_includes_session_id_field(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "_cache_dir", lambda: tmp_path)
    session_mod.save("sid-xyz", {"name": "p", "started_at": "x", "cwd_at_start": "/a"})
    entries = session_mod.iter_unanalyzed()
    assert len(entries) == 1
    assert entries[0]["session_id"] == "sid-xyz"
    assert entries[0]["name"] == "p"
