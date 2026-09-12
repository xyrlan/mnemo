"""Blocked sessions whose process is gone: re-bucketed, never dropped (#196).

A session that blocks and then dies leaves ``tempo=blocked`` frozen on disk,
and the queue replayed it forever as a live request for attention — sorted to
the top, named in the attach hint, counted in the statusline badge.

The rule everything here shares: a blocked session is *abandoned* when the
daemon roster proves its process is gone. Not when it is old. Age is not
evidence — a session genuinely waiting on a human for 24h is exactly what the
queue exists to surface.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from mnemo.core.sessions.jobs import Session, read_sessions
from mnemo.core.sessions.render import render_queue

DEAD_PID = 0  # never a real process; pid_alive rejects non-positive outright


def _blocked(short_id: str, *, live: bool | None = True, name: str = "s",
             updated_at: str = "2026-09-12T12:00:00.000Z", **kw) -> Session:
    return Session(short_id=short_id, state="working", tempo="blocked",
                   needs="q?", name=name, updated_at=updated_at, live=live, **kw)


# --- the Session field -----------------------------------------------------

def test_unknown_liveness_counts_as_waiting() -> None:
    """None means 'could not ask'. Never accuse a session we cannot probe."""
    assert _blocked("a", live=None).is_waiting is True
    assert _blocked("a", live=None).is_abandoned is False


def test_live_blocked_session_is_waiting() -> None:
    s = _blocked("a", live=True)

    assert s.is_waiting is True
    assert s.is_abandoned is False


def test_dead_blocked_session_is_abandoned_not_waiting() -> None:
    s = _blocked("a", live=False)

    assert s.is_abandoned is True
    assert s.is_waiting is False
    assert s.is_blocked is True  # the raw disk fact is unchanged


def test_a_dead_session_that_was_never_blocked_is_not_abandoned() -> None:
    """Abandoned is a blocked-only notion; a finished session is just done."""
    s = Session(short_id="a", state="done", tempo="idle", live=False)

    assert s.is_abandoned is False


# --- reading: liveness is resolved once, from the roster -------------------

def _job(root: Path, short_id: str, **fields) -> None:
    d = root / short_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(fields), encoding="utf-8")


def _roster(home: Path, workers: dict[str, dict]) -> None:
    d = home / "daemon"
    d.mkdir(parents=True, exist_ok=True)
    (d / "roster.json").write_text(json.dumps({"workers": workers}), encoding="utf-8")


def test_read_sessions_marks_a_rostered_session_live(tmp_path: Path) -> None:
    jobs_root, home = tmp_path / "jobs", tmp_path / "home"
    _job(jobs_root, "a3f1", state="working", tempo="blocked", needs="q?")
    _roster(home, {"a3f1": {"pid": os.getpid()}})

    (s,) = read_sessions(jobs_root, claude_home=home)

    assert s.live is True
    assert s.is_waiting is True


def test_read_sessions_marks_a_forgotten_session_dead(tmp_path: Path) -> None:
    jobs_root, home = tmp_path / "jobs", tmp_path / "home"
    _job(jobs_root, "a3f1", state="working", tempo="blocked", needs="q?")
    _roster(home, {"someone-else": {"pid": os.getpid()}})

    (s,) = read_sessions(jobs_root, claude_home=home)

    assert s.live is False
    assert s.is_abandoned is True


def test_read_sessions_leaves_liveness_unknown_without_a_roster(tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    _job(jobs_root, "a3f1", state="working", tempo="blocked", needs="q?")

    (s,) = read_sessions(jobs_root, claude_home=tmp_path / "no-home")

    assert s.live is None
    assert s.is_waiting is True  # unknown is treated as waiting


def test_roster_is_read_once_for_the_whole_queue(tmp_path: Path, monkeypatch) -> None:
    """Latency: the statusline runs this under a 2s timeout on every render."""
    jobs_root, home = tmp_path / "jobs", tmp_path / "home"
    for i in range(5):
        _job(jobs_root, f"s{i}", state="working", tempo="blocked", needs="q?")
    _roster(home, {})

    from mnemo.core.sessions import liveness as liveness_mod

    calls = []
    real = liveness_mod.read_roster
    monkeypatch.setattr(liveness_mod, "read_roster",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])

    read_sessions(jobs_root, claude_home=home)

    assert len(calls) == 1


# --- rendering: nothing is dropped, nothing dead sorts first ---------------

def test_abandoned_sessions_leave_the_waiting_bucket() -> None:
    out = render_queue([
        _blocked("dead", live=False, name="morta"),
        _blocked("alive", live=True, name="viva"),
    ])

    waiting = out.split("ABANDONADAS")[0]
    assert "viva" in waiting
    assert "morta" not in waiting
    assert "TE ESPERANDO (1)" in out


def test_abandoned_sessions_are_still_listed(tmp_path: Path) -> None:
    """Re-bucketed, never hidden: the user must still see and be able to rm it."""
    out = render_queue([_blocked("dead", live=False, name="morta")])

    assert "ABANDONADAS" in out
    assert "morta" in out
    assert "dead" in out


def test_abandoned_bucket_suggests_removal_not_attachment() -> None:
    out = render_queue([_blocked("e51f", live=False, name="morta")])

    assert "claude rm e51f" in out
    assert "claude attach" not in out


def test_waiting_sorts_newest_first_so_the_stalest_no_longer_wins() -> None:
    """Mislead #1: _sort_key put the oldest on top by construction."""
    out = render_queue([
        _blocked("old", live=True, name="antiga", updated_at="2026-09-01T12:00:00.000Z"),
        _blocked("new", live=True, name="recente", updated_at="2026-09-12T12:00:00.000Z"),
    ])

    assert out.index("recente") < out.index("antiga")


def test_attach_hint_names_the_freshest_waiting_session() -> None:
    """Mislead #2: render.py:92 picked blocked[0], the oldest."""
    out = render_queue([
        _blocked("old", live=True, name="antiga", updated_at="2026-09-01T12:00:00.000Z"),
        _blocked("new", live=True, name="recente", updated_at="2026-09-12T12:00:00.000Z"),
    ])

    assert "claude attach new" in out
    assert "claude attach old" not in out


def test_no_attach_hint_when_every_blocked_session_is_abandoned() -> None:
    out = render_queue([_blocked("dead", live=False, name="morta")])

    assert "claude attach" not in out


def test_a_session_with_no_timestamp_does_not_take_the_attach_hint() -> None:
    """Missing updatedAt must not masquerade as the freshest entry."""
    out = render_queue([
        _blocked("known", live=True, name="conhecida", updated_at="2026-09-12T12:00:00.000Z"),
        _blocked("nots", live=True, name="sem-data", updated_at=None),
    ])

    assert "claude attach known" in out


def test_abandoned_does_not_leak_into_working_or_done() -> None:
    out = render_queue([_blocked("dead", live=False, name="morta")])

    assert "TRABALHANDO" not in out
    assert "PRONTAS" not in out
