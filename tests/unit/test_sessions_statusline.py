"""The blocked-session count in the statusline.

Latency-critical: the composer runs on every render under a 2s timeout, so
this path reads state.json and nothing else. Any failure degrades to an
empty string rather than slowing or breaking the line.
"""
from __future__ import annotations

import json
from pathlib import Path

from mnemo import statusline


def _job(root: Path, short_id: str, **fields) -> None:
    d = root / short_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(fields), encoding="utf-8")


def test_empty_when_no_sessions(tmp_path: Path) -> None:
    assert statusline._blocked_segment(tmp_path) == ""


def test_empty_when_dir_missing(tmp_path: Path) -> None:
    assert statusline._blocked_segment(tmp_path / "nope") == ""


def test_empty_when_nothing_is_blocked(tmp_path: Path) -> None:
    _job(tmp_path, "w0", state="working", tempo="active")

    assert statusline._blocked_segment(tmp_path) == ""


def test_counts_blocked_sessions(tmp_path: Path) -> None:
    _job(tmp_path, "a", state="working", tempo="blocked", needs="q?")
    _job(tmp_path, "b", state="working", tempo="blocked", needs="q?")
    _job(tmp_path, "c", state="working", tempo="active")

    assert statusline._blocked_segment(tmp_path) == "2 esperando"


def test_singular_wording(tmp_path: Path) -> None:
    _job(tmp_path, "a", state="working", tempo="blocked", needs="q?")

    assert statusline._blocked_segment(tmp_path) == "1 esperando"


def test_malformed_state_does_not_break_the_line(tmp_path: Path) -> None:
    _job(tmp_path, "ok", state="working", tempo="blocked", needs="q?")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "state.json").write_text("{not json", encoding="utf-8")

    assert statusline._blocked_segment(tmp_path) == "1 esperando"
