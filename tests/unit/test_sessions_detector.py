"""Detecting the moment a blocked session gets its answer.

An answer to a blocked session is the highest-signal correction there is:
the maintainer is only consulted when it matters. The detector records where
that answer is written so extraction can treat it as such.

It cannot read timeline.jsonl — that file carries ``state`` transitions and
never mentions ``tempo`` (measured 2026-09-12). So it keeps ``lastTempo``
itself and compares.
"""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core.sessions import detector
from mnemo.core.sessions.jobs import Session


def _blocked(short_id: str = "a3f1") -> Session:
    return Session(short_id=short_id, state="working", tempo="blocked",
                   needs="answer: bcrypt ou argon2?", session_id="sid-1",
                   link_scan_path="/transcripts/sid-1.jsonl", cwd="/repo")


def _active(short_id: str = "a3f1") -> Session:
    return Session(short_id=short_id, state="working", tempo="active",
                   session_id="sid-1", link_scan_path="/transcripts/sid-1.jsonl",
                   cwd="/repo")


def _state(vault: Path) -> dict:
    return json.loads((vault / ".mnemo" / "session-queue.json").read_text(encoding="utf-8"))


def test_first_sighting_records_no_unblock(tmp_path: Path) -> None:
    detector.sweep([_blocked()], vault_root=tmp_path)

    assert _state(tmp_path)["seen"]["a3f1"]["lastTempo"] == "blocked"
    assert _state(tmp_path)["seen"]["a3f1"]["unblocks"] == []


def test_blocked_to_active_records_an_unblock(tmp_path: Path) -> None:
    detector.sweep([_blocked()], vault_root=tmp_path)
    detector.sweep([_active()], vault_root=tmp_path)

    (unblock,) = _state(tmp_path)["seen"]["a3f1"]["unblocks"]
    assert unblock["needs"] == "answer: bcrypt ou argon2?"
    assert unblock["linkScanPath"] == "/transcripts/sid-1.jsonl"
    assert unblock["sessionId"] == "sid-1"
    assert unblock["extracted"] is False
    assert unblock["at"]


def test_staying_blocked_records_nothing(tmp_path: Path) -> None:
    detector.sweep([_blocked()], vault_root=tmp_path)
    detector.sweep([_blocked()], vault_root=tmp_path)

    assert _state(tmp_path)["seen"]["a3f1"]["unblocks"] == []


def test_active_to_blocked_records_nothing(tmp_path: Path) -> None:
    detector.sweep([_active()], vault_root=tmp_path)
    detector.sweep([_blocked()], vault_root=tmp_path)

    assert _state(tmp_path)["seen"]["a3f1"]["unblocks"] == []


def test_repeated_sweeps_do_not_duplicate_an_unblock(tmp_path: Path) -> None:
    detector.sweep([_blocked()], vault_root=tmp_path)
    detector.sweep([_active()], vault_root=tmp_path)
    detector.sweep([_active()], vault_root=tmp_path)
    detector.sweep([_active()], vault_root=tmp_path)

    assert len(_state(tmp_path)["seen"]["a3f1"]["unblocks"]) == 1


def test_tracks_sessions_independently(tmp_path: Path) -> None:
    detector.sweep([_blocked("aaa"), _blocked("bbb")], vault_root=tmp_path)
    detector.sweep([_active("aaa"), _blocked("bbb")], vault_root=tmp_path)

    seen = _state(tmp_path)["seen"]
    assert len(seen["aaa"]["unblocks"]) == 1
    assert seen["bbb"]["unblocks"] == []


def test_corrupt_state_file_is_replaced_not_fatal(tmp_path: Path) -> None:
    (tmp_path / ".mnemo").mkdir(parents=True)
    (tmp_path / ".mnemo" / "session-queue.json").write_text("{not json", encoding="utf-8")

    detector.sweep([_blocked()], vault_root=tmp_path)

    assert _state(tmp_path)["seen"]["a3f1"]["lastTempo"] == "blocked"


def test_pending_unblocks_lists_unextracted_only(tmp_path: Path) -> None:
    detector.sweep([_blocked()], vault_root=tmp_path)
    detector.sweep([_active()], vault_root=tmp_path)

    pending = detector.pending_unblocks(vault_root=tmp_path)

    assert len(pending) == 1
    assert pending[0]["needs"] == "answer: bcrypt ou argon2?"
