"""Unit tests for the SessionEnd hint emitter."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from mnemo.hooks import session_end


def _write_state(vault_root: Path, last_run: str) -> None:
    d = vault_root / ".mnemo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "extraction-state.json").write_text(json.dumps({
        "schema_version": 1,
        "last_run": last_run,
        "entries": {},
    }))


def _touch_memory_file(vault_root: Path, agent: str, name: str, mtime: float | None = None) -> Path:
    d = vault_root / "bots" / agent / "memory"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(f"---\nname: {name}\ntype: feedback\n---\nbody")
    if mtime is not None:
        import os
        os.utime(p, (mtime, mtime))
    return p


def test_hint_not_emitted_below_threshold(tmp_vault: Path):
    _write_state(tmp_vault, "2026-04-13T00:00:00")
    now = time.time()
    for i in range(3):
        _touch_memory_file(tmp_vault, "a", f"f{i}.md", mtime=now)

    log_path = tmp_vault / "bots" / "a" / "logs" / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("---\ntags: [log]\n---\n# log\n")

    session_end._maybe_emit_hint(
        cfg={"extraction": {"hintThreshold": 5}},
        vault_root=tmp_vault,
        agent_name="a",
    )
    assert "🟡" not in log_path.read_text(encoding="utf-8")


def test_hint_emitted_at_threshold(tmp_vault: Path):
    _write_state(tmp_vault, "2026-04-13T00:00:00")
    now = time.time()
    for i in range(5):
        _touch_memory_file(tmp_vault, "a", f"f{i}.md", mtime=now)

    log_path = tmp_vault / "bots" / "a" / "logs" / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("---\ntags: [log]\n---\n# log\n")

    session_end._maybe_emit_hint(
        cfg={"extraction": {"hintThreshold": 5}},
        vault_root=tmp_vault,
        agent_name="a",
    )
    text = log_path.read_text(encoding="utf-8")
    assert "🟡" in text
    assert "5 new memories" in text
    assert "/mnemo extract" in text


def test_hint_emphatic_variant_at_triple_threshold(tmp_vault: Path):
    _write_state(tmp_vault, "2026-04-13T00:00:00")
    now = time.time()
    for i in range(16):
        _touch_memory_file(tmp_vault, "a", f"f{i}.md", mtime=now)
    log_path = tmp_vault / "bots" / "a" / "logs" / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("---\ntags: [log]\n---\n# log\n")

    session_end._maybe_emit_hint(
        cfg={"extraction": {"hintThreshold": 5}},
        vault_root=tmp_vault,
        agent_name="a",
    )
    assert "a lot!" in log_path.read_text(encoding="utf-8")


def test_hint_not_duplicated_same_day(tmp_vault: Path):
    _write_state(tmp_vault, "2026-04-13T00:00:00")
    now = time.time()
    for i in range(5):
        _touch_memory_file(tmp_vault, "a", f"f{i}.md", mtime=now)

    log_path = tmp_vault / "bots" / "a" / "logs" / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("---\ntags: [log]\n---\n# log\n")

    session_end._maybe_emit_hint({"extraction": {"hintThreshold": 5}}, tmp_vault, "a")
    session_end._maybe_emit_hint({"extraction": {"hintThreshold": 5}}, tmp_vault, "a")

    assert log_path.read_text(encoding="utf-8").count("🟡") == 1


def test_hint_silent_when_state_missing(tmp_vault: Path):
    # No state file written
    now = time.time()
    for i in range(10):
        _touch_memory_file(tmp_vault, "a", f"f{i}.md", mtime=now)
    log_path = tmp_vault / "bots" / "a" / "logs" / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("# log\n")

    session_end._maybe_emit_hint({"extraction": {"hintThreshold": 5}}, tmp_vault, "a")
    assert "🟡" not in log_path.read_text(encoding="utf-8")


def test_hint_silent_when_state_corrupt(tmp_vault: Path):
    (tmp_vault / ".mnemo").mkdir()
    (tmp_vault / ".mnemo" / "extraction-state.json").write_text("not json at all")
    now = time.time()
    for i in range(10):
        _touch_memory_file(tmp_vault, "a", f"f{i}.md", mtime=now)
    log_path = tmp_vault / "bots" / "a" / "logs" / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("# log\n")

    # Must not raise
    session_end._maybe_emit_hint({"extraction": {"hintThreshold": 5}}, tmp_vault, "a")
