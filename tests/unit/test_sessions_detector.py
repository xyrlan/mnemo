"""Detecting the moment a blocked session gets its answer.

An answer to a blocked session is the highest-signal correction there is:
the maintainer is only consulted when it matters. The detector records where
that answer is written so extraction can treat it as such.

It cannot read timeline.jsonl — that file carries ``state`` transitions and
never mentions ``tempo`` (measured 2026-09-12). So it keeps ``last_tempo``
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
    assert detector.sweep([_blocked()], vault_root=tmp_path) == 0

    assert _state(tmp_path)["seen"]["a3f1"]["last_tempo"] == "blocked"
    assert _state(tmp_path)["seen"]["a3f1"]["unblocks"] == []


def test_blocked_to_active_records_an_unblock(tmp_path: Path) -> None:
    assert detector.sweep([_blocked()], vault_root=tmp_path) == 0
    assert detector.sweep([_active()], vault_root=tmp_path) == 1

    (unblock,) = _state(tmp_path)["seen"]["a3f1"]["unblocks"]
    assert unblock["needs"] == "answer: bcrypt ou argon2?"
    assert unblock["link_scan_path"] == "/transcripts/sid-1.jsonl"
    assert unblock["session_id"] == "sid-1"
    assert unblock["extracted"] is False
    assert unblock["at"]


def test_staying_blocked_records_nothing(tmp_path: Path) -> None:
    detector.sweep([_blocked()], vault_root=tmp_path)
    assert detector.sweep([_blocked()], vault_root=tmp_path) == 0

    assert _state(tmp_path)["seen"]["a3f1"]["unblocks"] == []


def test_active_to_blocked_records_nothing(tmp_path: Path) -> None:
    detector.sweep([_active()], vault_root=tmp_path)
    assert detector.sweep([_blocked()], vault_root=tmp_path) == 0

    assert _state(tmp_path)["seen"]["a3f1"]["unblocks"] == []


def test_repeated_sweeps_do_not_duplicate_an_unblock(tmp_path: Path) -> None:
    detector.sweep([_blocked()], vault_root=tmp_path)
    assert detector.sweep([_active()], vault_root=tmp_path) == 1
    assert detector.sweep([_active()], vault_root=tmp_path) == 0
    assert detector.sweep([_active()], vault_root=tmp_path) == 0

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

    assert _state(tmp_path)["seen"]["a3f1"]["last_tempo"] == "blocked"


def test_pending_unblocks_lists_unextracted_only(tmp_path: Path) -> None:
    detector.sweep([_blocked()], vault_root=tmp_path)
    detector.sweep([_active()], vault_root=tmp_path)

    pending = detector.pending_unblocks(vault_root=tmp_path)

    assert len(pending) == 1
    assert pending[0]["needs"] == "answer: bcrypt ou argon2?"


def test_sweep_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    detector.sweep([_blocked()], vault_root=tmp_path)
    detector.sweep([_active()], vault_root=tmp_path)

    written = sorted(p.name for p in (tmp_path / ".mnemo").iterdir())

    assert written == ["session-queue.json"]


def test_an_old_camelcase_file_re_baselines(tmp_path: Path) -> None:
    """No migration by design: the state is disposable and the feature is
    unreleased, so a file written under the old keys just starts over."""
    (tmp_path / ".mnemo").mkdir(parents=True)
    (tmp_path / ".mnemo" / "session-queue.json").write_text(
        json.dumps({"seen": {"a3f1": {"lastTempo": "blocked", "unblocks": []}}}),
        encoding="utf-8",
    )

    # The stale lastTempo is invisible under the new name, so this reads as a
    # first sighting and records nothing rather than a phantom unblock.
    assert detector.sweep([_active()], vault_root=tmp_path) == 0

    entry = _state(tmp_path)["seen"]["a3f1"]
    assert entry["last_tempo"] == "active"
    assert entry["unblocks"] == []


def _writes(monkeypatch) -> list[Path]:
    """Collect every real write a sweep stages, rather than watching mtime.

    mtime granularity is coarse enough that two writes in the same test can
    look identical, which would let the no-churn test pass for the wrong
    reason. Counting the writer's calls cannot.
    """
    written: list[Path] = []
    real = detector.atomic_write_bytes

    def spy(path, data):
        written.append(Path(path))
        return real(path, data)

    monkeypatch.setattr(detector, "atomic_write_bytes", spy)
    return written


def test_an_unchanged_sweep_does_not_rewrite_the_file(tmp_path: Path, monkeypatch) -> None:
    """``--watch`` re-reads every two seconds; a still queue must not churn."""
    detector.sweep([_blocked()], vault_root=tmp_path)

    written = _writes(monkeypatch)
    detector.sweep([_blocked()], vault_root=tmp_path)
    detector.sweep([_blocked()], vault_root=tmp_path)

    assert written == []


def test_a_recorded_unblock_writes(tmp_path: Path, monkeypatch) -> None:
    detector.sweep([_blocked()], vault_root=tmp_path)

    written = _writes(monkeypatch)
    assert detector.sweep([_active()], vault_root=tmp_path) == 1

    assert len(written) == 1
    assert _state(tmp_path)["seen"]["a3f1"]["unblocks"]


def test_a_brand_new_session_writes(tmp_path: Path, monkeypatch) -> None:
    """A first sighting records no unblock, but it does add a ``seen`` entry."""
    detector.sweep([_blocked("aaa")], vault_root=tmp_path)

    written = _writes(monkeypatch)
    assert detector.sweep([_blocked("aaa"), _blocked("bbb")], vault_root=tmp_path) == 0

    assert len(written) == 1
    assert set(_state(tmp_path)["seen"]) == {"aaa", "bbb"}


def test_a_tempo_change_with_no_unblock_still_writes(tmp_path: Path, monkeypatch) -> None:
    """``active -> idle`` records nothing, but ``last_tempo`` must still move:
    the next ``blocked -> active`` edge is computed against that value."""
    detector.sweep([_active()], vault_root=tmp_path)

    written = _writes(monkeypatch)
    idle = Session(short_id="a3f1", state="working", tempo="idle",
                   session_id="sid-1", link_scan_path="/transcripts/sid-1.jsonl",
                   cwd="/repo")
    assert detector.sweep([idle], vault_root=tmp_path) == 0

    assert len(written) == 1
    assert _state(tmp_path)["seen"]["a3f1"]["last_tempo"] == "idle"


def test_a_needs_change_while_still_blocked_writes(tmp_path: Path, monkeypatch) -> None:
    """``last_needs`` is the label the eventual unblock carries, so a new
    question replacing an old one has to reach disk before the answer lands."""
    detector.sweep([_blocked()], vault_root=tmp_path)

    written = _writes(monkeypatch)
    second = Session(short_id="a3f1", state="working", tempo="blocked",
                     needs="answer: postgres ou sqlite?", session_id="sid-1",
                     link_scan_path="/transcripts/sid-1.jsonl", cwd="/repo")
    assert detector.sweep([second], vault_root=tmp_path) == 0

    assert len(written) == 1
    assert _state(tmp_path)["seen"]["a3f1"]["last_needs"] == "answer: postgres ou sqlite?"
