"""Consuming the unblock markers the detector records.

The detector writes a marker every time a blocked session gets answered.
Until now nothing read those markers and nothing ever set ``extracted``
(#195). These tests pin the consumer and, more importantly, pin the *wiring*:
this repo has now shipped a writer with no reader twice (the reflex
calibrator, then this), and both times every test passed because every test
exercised the writer.

Why the consumer runs ``learn`` at all, rather than merely tagging a
transcript region as interesting: the automatic path never sees these
sessions. ``session_end`` briefs with the default ``min_mutations=1``, and a
session whose only product is the maintainer answering a question mutates no
files, so it is skipped outright. Measured over 206 real transcripts, 76
(37%) have zero mutations — but only 15 of those carry correction-shaped user
text, so briefing every quiet session would buy 61 wasted LLM calls. The
unblock edge is the selector that separates the two.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from mnemo.core.sessions import detector, unblocks
from mnemo.core.sessions.jobs import Session


def _blocked(short_id: str = "a3f1", session_id: str = "sid-1") -> Session:
    return Session(short_id=short_id, state="working", tempo="blocked",
                   needs="use argon2, never bcrypt", session_id=session_id,
                   link_scan_path=f"/transcripts/{session_id}.jsonl", cwd="/repo")


def _active(short_id: str = "a3f1", session_id: str = "sid-1") -> Session:
    return Session(short_id=short_id, state="working", tempo="active",
                   session_id=session_id,
                   link_scan_path=f"/transcripts/{session_id}.jsonl", cwd="/repo")


def _record_one(vault: Path, short_id: str = "a3f1", session_id: str = "sid-1") -> None:
    detector.sweep([_blocked(short_id, session_id)], vault_root=vault)
    detector.sweep([_active(short_id, session_id)], vault_root=vault)


def _state(vault: Path) -> dict:
    return json.loads((vault / ".mnemo" / "session-queue.json").read_text(encoding="utf-8"))


class _Report:
    """Stand-in for ``learn.LearnReport``: only the fields the consumer reads."""

    def __init__(self, *, learned=None, error="", corrections=0):
        self.learned = learned if learned is not None else [{"slug": "s", "name": "n"}]
        self.error = error
        self.corrections = corrections
        self.staged = 0


# --- the consumer ---------------------------------------------------------


def test_consuming_learns_from_the_unblocked_session(monkeypatch, tmp_path: Path) -> None:
    """The marker's payload *is* the call: cwd and session_id, both recorded."""
    _record_one(tmp_path)
    calls: list[dict] = []

    def _fake_learn(cfg, *, cwd, session_id, **kw):
        calls.append({"cwd": cwd, "session_id": session_id, "kw": kw})
        return _Report()

    monkeypatch.setattr("mnemo.core.learn.learn", _fake_learn)

    report = unblocks.consume({}, vault_root=tmp_path)

    assert [c["cwd"] for c in calls] == ["/repo"]
    assert [c["session_id"] for c in calls] == ["sid-1"]
    assert report.consumed == 1


def test_consuming_marks_the_entry_extracted(monkeypatch, tmp_path: Path) -> None:
    """The flag the original code never wrote. Without this the list grows
    forever and every run re-learns the same session."""
    _record_one(tmp_path)
    monkeypatch.setattr("mnemo.core.learn.learn", lambda *a, **k: _Report())

    unblocks.consume({}, vault_root=tmp_path)

    (entry,) = _state(tmp_path)["seen"]["a3f1"]["unblocks"]
    assert entry["extracted"] is True
    assert detector.pending_unblocks(vault_root=tmp_path) == []


def test_a_second_run_does_no_work(monkeypatch, tmp_path: Path) -> None:
    _record_one(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        "mnemo.core.learn.learn",
        lambda cfg, *, cwd, session_id, **k: calls.append(session_id) or _Report(),
    )

    unblocks.consume({}, vault_root=tmp_path)
    second = unblocks.consume({}, vault_root=tmp_path)

    assert calls == ["sid-1"]
    assert second.consumed == 0


def test_each_session_is_consumed_once(monkeypatch, tmp_path: Path) -> None:
    _record_one(tmp_path, "a3f1", "sid-1")
    _record_one(tmp_path, "b7c2", "sid-2")
    calls: list[str] = []
    monkeypatch.setattr(
        "mnemo.core.learn.learn",
        lambda cfg, *, cwd, session_id, **k: calls.append(session_id) or _Report(),
    )

    report = unblocks.consume({}, vault_root=tmp_path)

    assert sorted(calls) == ["sid-1", "sid-2"]
    assert report.consumed == 2


# --- failure is not silent, and not fatal ---------------------------------


def test_a_failed_learn_leaves_the_marker_pending(monkeypatch, tmp_path: Path) -> None:
    """A held lock or a missing transcript is transient. Marking it consumed
    would discard the signal for good, which is the bug this issue is about."""
    _record_one(tmp_path)
    monkeypatch.setattr(
        "mnemo.core.learn.learn",
        lambda *a, **k: _Report(error="another extraction is in progress"),
    )

    report = unblocks.consume({}, vault_root=tmp_path)

    assert report.consumed == 0
    assert report.failed == 1
    assert len(detector.pending_unblocks(vault_root=tmp_path)) == 1


def test_a_raising_learn_does_not_abort_the_others(monkeypatch, tmp_path: Path) -> None:
    _record_one(tmp_path, "a3f1", "sid-1")
    _record_one(tmp_path, "b7c2", "sid-2")

    def _boom(cfg, *, cwd, session_id, **k):
        if session_id == "sid-1":
            raise RuntimeError("transcript vanished")
        return _Report()

    monkeypatch.setattr("mnemo.core.learn.learn", _boom)

    report = unblocks.consume({}, vault_root=tmp_path)

    assert report.consumed == 1
    assert report.failed == 1
    pending = detector.pending_unblocks(vault_root=tmp_path)
    assert [u["session_id"] for u in pending] == ["sid-1"]


def test_a_marker_without_a_session_id_is_retired_not_retried(
    monkeypatch, tmp_path: Path
) -> None:
    """``session_id`` is optional on ``Session``. A marker that can never
    resolve to a transcript is dead weight — retry it forever and it is the
    append-only list all over again."""
    _record_one(tmp_path)
    state = _state(tmp_path)
    state["seen"]["a3f1"]["unblocks"][0]["session_id"] = None
    (tmp_path / ".mnemo" / "session-queue.json").write_text(
        json.dumps(state), encoding="utf-8"
    )
    monkeypatch.setattr(
        "mnemo.core.learn.learn",
        lambda *a, **k: pytest.fail("must not learn without a session id"),
    )

    report = unblocks.consume({}, vault_root=tmp_path)

    assert report.skipped == 1
    assert detector.pending_unblocks(vault_root=tmp_path) == []


def test_nothing_pending_does_not_touch_the_state_file(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "mnemo.core.learn.learn", lambda *a, **k: pytest.fail("nothing to learn")
    )

    report = unblocks.consume({}, vault_root=tmp_path)

    assert report.consumed == 0
    assert not (tmp_path / ".mnemo" / "session-queue.json").exists()


def test_the_learned_slugs_are_reported(monkeypatch, tmp_path: Path) -> None:
    """The caller prints these; an unblock that taught nothing must be
    distinguishable from one that taught something."""
    _record_one(tmp_path)
    monkeypatch.setattr(
        "mnemo.core.learn.learn",
        lambda *a, **k: _Report(learned=[{"slug": "argon2-not-bcrypt", "name": "Use argon2"}]),
    )

    report = unblocks.consume({}, vault_root=tmp_path)

    assert report.learned == [{"slug": "argon2-not-bcrypt", "name": "Use argon2"}]


def test_two_edges_in_the_same_second_are_not_confused(monkeypatch, tmp_path: Path) -> None:
    """``at`` has seconds resolution, so a session answered twice inside one
    second writes two markers with identical timestamps. Identifying a marker
    by its content would retire both when only one was learned — discarding a
    correction, which is the whole failure this issue is about.
    """
    for _ in range(2):
        _record_one(tmp_path)
    assert len(detector.pending_unblocks(vault_root=tmp_path)) == 2

    calls: list[str] = []

    def _one_then_fail(cfg, *, cwd, session_id, **k):
        calls.append(session_id)
        if len(calls) == 1:
            return _Report()
        return _Report(error="another extraction is in progress")

    monkeypatch.setattr("mnemo.core.learn.learn", _one_then_fail)

    report = unblocks.consume({}, vault_root=tmp_path)

    assert (report.consumed, report.failed) == (1, 1)
    # Exactly one marker retired; the failed one must survive for a retry.
    assert len(detector.pending_unblocks(vault_root=tmp_path)) == 1


# --- the wiring: a writer with no reader is the bug class ------------------


def _calls_in(path: Path, name: str) -> list[str]:
    """Every function in ``path`` whose body mentions the call ``name(...)``.

    Parsed rather than grepped so a mention inside a docstring or a comment —
    exactly what ``detector``'s module docstring is full of — cannot satisfy
    the assertion.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            func = sub.func
            called = getattr(func, "attr", None) or getattr(func, "id", None)
            if called == name:
                found.append(node.name)
                break
    return found


def test_pending_unblocks_has_a_production_caller() -> None:
    """#195: ``pending_unblocks`` was written, documented, tested — and never
    called. So was the reflex calibrator before PR #106. A writer with no
    reader passes every test that exercises the writer, so the reader has to
    be asserted directly.
    """
    src = Path(__file__).resolve().parents[2] / "src" / "mnemo"
    callers = [
        f"{p.relative_to(src)}::{fn}"
        for p in src.rglob("*.py")
        for fn in _calls_in(p, "pending_unblocks")
    ]

    assert callers, (
        "pending_unblocks() has no caller in src/ — the unblock markers are "
        "being recorded and discarded again (#195)"
    )


def test_something_in_production_marks_an_unblock_extracted() -> None:
    """The other half of the same bug: the flag existed in the written record
    but no code path ever set it, so ``pending_unblocks`` would have returned
    a growing list forever even once it had a caller."""
    src = Path(__file__).resolve().parents[2] / "src" / "mnemo"
    writers = [
        p for p in src.rglob("*.py")
        if "extracted" in p.read_text(encoding="utf-8")
        and "unblock" in p.read_text(encoding="utf-8").lower()
    ]

    assert writers, "nothing in src/ sets an unblock's `extracted` flag (#195)"


def test_the_consumer_is_reachable_from_a_real_entry_point() -> None:
    """Being called by another unused function would satisfy the test above
    while leaving the wire just as dead. Pin the actual trigger."""
    src = Path(__file__).resolve().parents[2] / "src" / "mnemo"
    reachable = [
        f"{p.relative_to(src)}::{fn}"
        for p in src.rglob("*.py")
        for fn in _calls_in(p, "consume")
        if p.name != "unblocks.py"
    ]

    assert reachable, (
        "unblocks.consume() is defined but never invoked — wire it to a CLI "
        "command or a hook (#195)"
    )
