"""Shared atomic write — the concurrent-writer race, solved once.

Three writers already re-derived this pattern (session cache got the correct
mkstemp version after the 2026-07-31 crash; reflex and rule-activation
indexes kept the fixed `<target>.tmp` name and reflex logged four
FileNotFoundError collisions in two weeks when concurrent SessionStarts
rebuilt at once). The helper is the single correct copy.
"""
from __future__ import annotations

import json
import os
import threading

import pytest

from mnemo.core.atomic import atomic_write_bytes


def test_writes_content_readably(tmp_path):
    target = tmp_path / "out.json"
    atomic_write_bytes(target, b'{"a": 1}')
    assert json.loads(target.read_text()) == {"a": 1}


def test_overwrites_existing_target(tmp_path):
    target = tmp_path / "out.json"
    target.write_text("old")
    atomic_write_bytes(target, b"new")
    assert target.read_text() == "new"


def test_creates_parent_dirs(tmp_path):
    target = tmp_path / "deep" / "nested" / "out.json"
    atomic_write_bytes(target, b"x")
    assert target.read_text() == "x"


def test_each_call_uses_its_own_tmp_file(tmp_path, monkeypatch):
    """The race regression guard: a fixed `<target>.tmp` name lets one
    process' os.replace consume the other's tmp file. Every call must stage
    through a distinct tmp path."""
    sources = []
    real_replace = os.replace

    def spy(src, dst, *a, **kw):
        sources.append(str(src))
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", spy)
    target = tmp_path / "out.json"
    atomic_write_bytes(target, b"one")
    atomic_write_bytes(target, b"two")
    assert len(sources) == 2
    assert sources[0] != sources[1]


def test_no_tmp_files_left_behind(tmp_path):
    target = tmp_path / "out.json"
    for i in range(3):
        atomic_write_bytes(target, str(i).encode())
    assert [p.name for p in tmp_path.iterdir()] == ["out.json"]


def test_concurrent_writers_never_raise(tmp_path):
    """The observed failure mode: concurrent SessionStarts rebuilding the
    same index. Any writer raising FileNotFoundError here is the bug."""
    target = tmp_path / "index.json"
    errors: list[Exception] = []

    def hammer(n: int) -> None:
        try:
            for i in range(20):
                atomic_write_bytes(target, f'{{"writer": {n}, "i": {i}}}'.encode())
        except Exception as exc:  # noqa: BLE001 — the test IS about exceptions
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    # Last write wins; whatever survived must be one writer's intact payload.
    data = json.loads(target.read_text())
    assert set(data) == {"writer", "i"}


def test_failed_replace_cleans_tmp_and_raises(tmp_path, monkeypatch):
    def boom(src, dst, *a, **kw):
        raise PermissionError("locked")

    monkeypatch.setattr(os, "replace", boom)
    target = tmp_path / "out.json"
    with pytest.raises(OSError):
        atomic_write_bytes(target, b"x")
    assert list(tmp_path.iterdir()) == []
