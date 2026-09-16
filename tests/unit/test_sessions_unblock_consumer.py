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
import time
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


# --- one pass at a time, and a bounded one (#329) -------------------------


def _record_many(vault: Path, count: int) -> None:
    """*count* markers, each on its own session, recorded oldest first."""
    for n in range(count):
        _record_one(vault, short_id=f"s{n:02d}", session_id=f"sid-{n:02d}")


def test_a_pass_stops_at_the_bound(monkeypatch, tmp_path: Path) -> None:
    """40 markers is a backlog, not a burst to clear in one process.

    Every marker is a briefing plus an extraction — two or more `claude`
    subprocesses — so an unbounded pass runs for minutes while every session
    that ends meanwhile spawns another one (#329). The rest are not lost: they
    stay pending and the next SessionEnd takes the next five.
    """
    _record_many(tmp_path, 12)
    seen: list[str] = []

    monkeypatch.setattr(
        "mnemo.core.learn.learn",
        lambda cfg, *, cwd, session_id, **kw: seen.append(session_id) or _Report(),
    )

    report = unblocks.consume({}, vault_root=tmp_path)

    assert len(seen) == unblocks.MAX_PER_PASS
    assert report.consumed == unblocks.MAX_PER_PASS
    assert report.remaining == 12 - unblocks.MAX_PER_PASS
    # The untouched markers are still pending, not dropped.
    assert len(detector.pending_unblocks(vault_root=tmp_path)) == 12 - unblocks.MAX_PER_PASS


def test_the_bound_takes_the_oldest_markers_first(monkeypatch, tmp_path: Path) -> None:
    """A backlog drains in the order it accumulated. The newest marker is the
    one the next pass will reach in minutes; the oldest has already waited."""
    _record_many(tmp_path, 4)
    state = _state(tmp_path)
    stamps = ["2026-09-15T20:0{}:00+00:00".format(n) for n in (3, 1, 4, 2)]
    for (short_id, entry), at in zip(sorted(state["seen"].items()), stamps):
        entry["unblocks"][0]["at"] = at
    (tmp_path / ".mnemo" / "session-queue.json").write_text(json.dumps(state), encoding="utf-8")

    seen: list[str] = []
    monkeypatch.setattr(
        "mnemo.core.learn.learn",
        lambda cfg, *, cwd, session_id, **kw: seen.append(session_id) or _Report(),
    )

    unblocks.consume({}, vault_root=tmp_path, limit=2)

    assert seen == ["sid-01", "sid-03"]


def test_a_second_pass_exits_instead_of_duplicating_the_first(
    monkeypatch, tmp_path: Path
) -> None:
    """The shape of #329: N sessions ending together spawned N sweeps, each
    one running the same LLM calls over the same markers.

    The inner pass here stands in for the concurrent sweep — it is started
    while the outer one holds the lock, which is exactly the race, and it must
    learn nothing and say so rather than reporting an empty success.
    """
    _record_many(tmp_path, 2)
    inner: list = []
    outer_calls: list[str] = []

    def _learn(cfg, *, cwd, session_id, **kw):
        outer_calls.append(session_id)
        if not inner:
            inner.append(unblocks.consume({}, vault_root=tmp_path))
        return _Report()

    monkeypatch.setattr("mnemo.core.learn.learn", _learn)

    report = unblocks.consume({}, vault_root=tmp_path)

    assert report.consumed == 2
    assert inner[0].locked is True
    assert inner[0].consumed == 0
    # Two markers, two learns: the nested pass added none of its own.
    assert len(outer_calls) == 2


def test_the_lock_is_released_when_a_pass_ends(monkeypatch, tmp_path: Path) -> None:
    """A lock left behind would wedge the feature until it went stale."""
    _record_one(tmp_path)
    monkeypatch.setattr(
        "mnemo.core.learn.learn", lambda cfg, *, cwd, session_id, **kw: _Report()
    )

    unblocks.consume({}, vault_root=tmp_path)

    assert not unblocks.lock_path(tmp_path).exists()
    assert unblocks.sweep_in_flight(tmp_path) is False


def test_nothing_pending_never_touches_the_lock(tmp_path: Path) -> None:
    """The overwhelmingly common pass. A mkdir per session end to discover an
    empty list is the cost the cheap check exists to avoid."""
    report = unblocks.consume({}, vault_root=tmp_path)

    assert report.locked is False
    assert not (tmp_path / ".mnemo").exists()


def test_session_end_does_not_spawn_into_a_running_sweep(tmp_path, monkeypatch) -> None:
    """The hook's own half: a stat instead of a process that could only find
    the lock held and exit. The lock is still what enforces it."""
    from mnemo.hooks import session_end

    monkeypatch.setattr(
        "mnemo.core.sessions.detector.pending_unblocks",
        lambda *, vault_root: [{"session_id": "sid-1", "cwd": "/repo"}],
    )
    monkeypatch.setattr(
        session_end,
        "_spawn_detached_unblock_consumption",
        lambda: pytest.fail("must not spawn while a sweep holds the lock"),
    )
    unblocks.lock_path(tmp_path).mkdir(parents=True)

    session_end._maybe_consume_unblocks({"briefings": {"enabled": True}}, tmp_path)


def test_a_stale_lock_does_not_wedge_the_sweep(monkeypatch, tmp_path: Path) -> None:
    """A hard-killed sweep leaves its lock behind; past the TTL the next pass
    takes it rather than waiting forever."""
    import os

    _record_one(tmp_path)
    lock = unblocks.lock_path(tmp_path)
    lock.mkdir(parents=True)
    old = time.time() - unblocks.LOCK_STALE_SECONDS - 60
    os.utime(lock, (old, old))

    monkeypatch.setattr(
        "mnemo.core.learn.learn", lambda cfg, *, cwd, session_id, **kw: _Report()
    )

    assert unblocks.sweep_in_flight(tmp_path) is False
    assert unblocks.consume({}, vault_root=tmp_path).consumed == 1
