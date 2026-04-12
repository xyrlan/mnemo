from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from mnemo.core import locks


def test_lock_acquires_when_free(tmp_path: Path):
    with locks.try_lock(tmp_path / "x.lock") as held:
        assert held is True
        assert (tmp_path / "x.lock").exists()
    # released after context exit
    assert not (tmp_path / "x.lock").exists()


def test_lock_returns_false_when_held(tmp_path: Path):
    lock = tmp_path / "x.lock"
    lock.mkdir()  # simulate live lock
    # Make it fresh so stale-recovery doesn't reclaim
    os.utime(lock, None)
    with locks.try_lock(lock) as held:
        assert held is False
    # We did not own it, so it must still exist
    assert lock.exists()


def test_stale_lock_is_reclaimed(tmp_path: Path):
    lock = tmp_path / "x.lock"
    lock.mkdir()
    # Set mtime to 10 minutes ago
    old = time.time() - 600
    os.utime(lock, (old, old))
    with locks.try_lock(lock, stale_after=60.0) as held:
        assert held is True


def test_nonblocking_does_not_wait(tmp_path: Path):
    lock = tmp_path / "x.lock"
    lock.mkdir()
    os.utime(lock, None)
    start = time.time()
    with locks.try_lock(lock) as held:
        assert held is False
    assert time.time() - start < 0.5
