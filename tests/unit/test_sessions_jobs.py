"""Reading Claude Code background-session state (the live session queue).

``state.json`` is written by Claude Code, never by mnemo. Every field is
optional from our side: a schema change upstream must degrade the render,
never raise. The one field that carries the whole feature is ``tempo`` —
see :mod:`mnemo.core.sessions.jobs`.
"""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core.sessions import jobs


def _job(root: Path, short_id: str, **fields) -> Path:
    d = root / short_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(fields), encoding="utf-8")
    return d


def test_returns_empty_when_jobs_dir_missing(tmp_path: Path) -> None:
    assert jobs.read_sessions(tmp_path / "nope") == []


def test_reads_a_blocked_session(tmp_path: Path) -> None:
    _job(
        tmp_path, "a3f1",
        state="working", tempo="blocked",
        needs="answer: bcrypt ou argon2?",
        detail="reading auth.py",
        name="auth-refactor",
        cwd="/repo",
        tokens=1200,
    )

    (s,) = jobs.read_sessions(tmp_path)

    assert s.short_id == "a3f1"
    assert s.is_blocked is True
    assert s.needs == "answer: bcrypt ou argon2?"
    assert s.name == "auth-refactor"
    assert s.tokens == 1200


def test_blocked_is_driven_by_tempo_not_state(tmp_path: Path) -> None:
    # The regression guard for the whole feature. A session waiting on a human
    # sits at state=working, tempo=blocked. Reading ``state`` files it under
    # "working" and the queue stops being a queue.
    _job(tmp_path, "a3f1", state="working", tempo="blocked", needs="q?")

    (s,) = jobs.read_sessions(tmp_path)

    assert s.is_blocked is True
    assert s.is_done is False


def test_done_is_driven_by_state(tmp_path: Path) -> None:
    _job(tmp_path, "e51f", state="done", tempo="idle", name="shipped")

    (s,) = jobs.read_sessions(tmp_path)

    assert s.is_done is True
    assert s.is_blocked is False


def test_missing_fields_degrade_and_never_raise(tmp_path: Path) -> None:
    _job(tmp_path, "bare")  # every field absent

    (s,) = jobs.read_sessions(tmp_path)

    assert s.short_id == "bare"
    assert s.needs is None
    assert s.name is None
    assert s.tokens is None
    assert s.is_blocked is False


def test_malformed_json_skips_that_session_only(tmp_path: Path) -> None:
    _job(tmp_path, "good", state="working", tempo="blocked", needs="q?")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "state.json").write_text("{not json", encoding="utf-8")

    ids = [s.short_id for s in jobs.read_sessions(tmp_path)]

    assert ids == ["good"]


def test_pins_json_and_loose_files_are_ignored(tmp_path: Path) -> None:
    _job(tmp_path, "good", state="working", tempo="active")
    (tmp_path / "pins.json").write_text("{}", encoding="utf-8")

    assert [s.short_id for s in jobs.read_sessions(tmp_path)] == ["good"]


def test_filters_by_cwd_when_asked(tmp_path: Path) -> None:
    _job(tmp_path, "here", state="working", tempo="active", cwd="/repo/a")
    _job(tmp_path, "elsewhere", state="working", tempo="active", cwd="/repo/b")

    ids = [s.short_id for s in jobs.read_sessions(tmp_path, cwd="/repo/a")]

    assert ids == ["here"]


def test_suggested_reply_is_optional(tmp_path: Path) -> None:
    _job(tmp_path, "with", tempo="blocked", needs="q?", suggestedReply="sim")
    _job(tmp_path, "without", tempo="blocked", needs="q?")

    by_id = {s.short_id: s for s in jobs.read_sessions(tmp_path)}

    assert by_id["with"].suggested_reply == "sim"
    assert by_id["without"].suggested_reply is None
