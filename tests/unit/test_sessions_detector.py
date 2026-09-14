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


# --- the transcript is the record that outlives the edge (#176) -------------
#
# ``tempo`` flips back to ``blocked`` about ten seconds after an answer lands,
# and every shipped trigger fires on an event uncorrelated with that window
# (measured, PR #203). So the sweep does not sample ``tempo`` for the edge any
# more: it reads the session's transcript forward from its own bookmark, and
# a human turn found there *is* the edge — however late the sweep runs.


def _rec(kind: str, text: str, ts: str, **extra) -> str:
    """One transcript line in Claude Code's shape, compact like the real file."""
    if kind == "assistant":
        message = {"role": "assistant", "content": [{"type": "text", "text": text}]}
    elif kind == "tool_result":
        kind = "user"
        message = {"role": "user", "content": [{"type": "tool_result", "content": text}]}
    else:
        message = {"role": "user", "content": text}
    record = {"type": kind, "message": message, "timestamp": ts, **extra}
    return json.dumps(record, separators=(",", ":")) + "\n"


_OPENING = _rec("user", "read README first section, then STOP and ask me", "2026-09-12T14:44:30.915Z")
_QUESTION = _rec("assistant", "Which language should the summary be in?", "2026-09-12T14:44:40.000Z")
_ANSWER = _rec("user", "Portuguese, and open with a bilingual glossary.", "2026-09-12T14:46:25.309Z")
_FOLLOW_UP = _rec("assistant", "Done. One more question: include the badges?", "2026-09-12T14:46:35.000Z")


def _transcript(tmp_path: Path, *lines: str, name: str = "sid-1.jsonl") -> Path:
    path = tmp_path / "transcripts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _session(path: Path, tempo: str = "blocked", needs: str | None = None,
             short_id: str = "a3f1") -> Session:
    return Session(short_id=short_id, state="working", tempo=tempo, needs=needs,
                   session_id="sid-1", link_scan_path=str(path), cwd="/repo")


def _entry(vault: Path, short_id: str = "a3f1") -> dict:
    return _state(vault)["seen"][short_id]


def test_a_human_turn_that_landed_between_sweeps_is_recorded(tmp_path: Path) -> None:
    """The measured miss: answered and re-blocked before anyone swept.

    Both sweeps see ``tempo=blocked``, so the old comparison records nothing.
    The transcript still holds the answer, and that is what gets recorded.
    """
    path = _transcript(tmp_path, _OPENING, _QUESTION)
    assert detector.sweep([_session(path, "blocked")], vault_root=tmp_path) == 0

    with path.open("a", encoding="utf-8") as fh:
        fh.write(_ANSWER + _FOLLOW_UP)

    assert detector.sweep([_session(path, "blocked")], vault_root=tmp_path) == 1

    (unblock,) = _entry(tmp_path)["unblocks"]
    assert unblock["answered_at"] == "2026-09-12T14:46:25.309Z"
    assert unblock["answer"] == "Portuguese, and open with a bilingual glossary."
    assert unblock["needs"] == "Which language should the summary be in?"
    assert unblock["session_id"] == "sid-1"
    assert unblock["link_scan_path"] == str(path)
    assert unblock["cwd"] == "/repo"
    assert unblock["extracted"] is False
    assert unblock["at"]


def test_a_first_sighting_baselines_at_the_end_and_records_nothing(tmp_path: Path) -> None:
    """History is not an edge: a transcript full of old answers, seen for the
    first time, must not flood the consumer with one learn call per turn."""
    path = _transcript(tmp_path, _OPENING, _QUESTION, _ANSWER, _FOLLOW_UP, _ANSWER)

    assert detector.sweep([_session(path)], vault_root=tmp_path) == 0

    entry = _entry(tmp_path)
    assert entry["unblocks"] == []
    assert entry["offset"] == path.stat().st_size
    assert entry["transcript"] == str(path)


def test_the_opening_prompt_is_not_an_edge(tmp_path: Path) -> None:
    """A session seen before its transcript has anything in it: the task that
    starts it is the first human text to arrive, and nothing was blocked yet."""
    path = _transcript(tmp_path)  # empty: state.json can exist first
    assert detector.sweep([_session(path)], vault_root=tmp_path) == 0

    path.write_text(_OPENING + _QUESTION, encoding="utf-8")
    assert detector.sweep([_session(path)], vault_root=tmp_path) == 0
    assert _entry(tmp_path)["unblocks"] == []

    with path.open("a", encoding="utf-8") as fh:
        fh.write(_ANSWER)
    assert detector.sweep([_session(path)], vault_root=tmp_path) == 1


def test_a_first_sighting_before_any_assistant_turn_still_skips_the_opening(tmp_path: Path) -> None:
    """Seen with the opening prompt on disk but no reply yet. The bookmark
    sits after the opening prompt, so the only way to know it was the opening
    is to know no assistant turn had happened before the bookmark."""
    path = _transcript(tmp_path, _OPENING)
    assert detector.sweep([_session(path)], vault_root=tmp_path) == 0
    assert _entry(tmp_path)["primed"] is False

    with path.open("a", encoding="utf-8") as fh:
        fh.write(_QUESTION + _ANSWER)
    assert detector.sweep([_session(path)], vault_root=tmp_path) == 1
    assert _entry(tmp_path)["primed"] is True


def test_a_first_sighting_after_an_assistant_turn_counts_the_next_human_turn(tmp_path: Path) -> None:
    """The common case: the session has been running for a while when first
    seen. The next human turn arrives with no assistant record in the scanned
    region before it, and it must still count."""
    path = _transcript(tmp_path, _OPENING, _QUESTION)
    detector.sweep([_session(path)], vault_root=tmp_path)
    assert _entry(tmp_path)["primed"] is True

    with path.open("a", encoding="utf-8") as fh:
        fh.write(_ANSWER)
    assert detector.sweep([_session(path)], vault_root=tmp_path) == 1


def test_synthetic_prompts_and_tool_results_are_not_edges(tmp_path: Path) -> None:
    path = _transcript(tmp_path, _OPENING, _QUESTION)
    detector.sweep([_session(path)], vault_root=tmp_path)

    with path.open("a", encoding="utf-8") as fh:
        fh.write(_rec("tool_result", "file contents", "2026-09-12T14:45:00.000Z"))
        fh.write(_rec("user", "<task-notification>\n<task-id>x</task-id>", "2026-09-12T14:45:01.000Z"))
        fh.write(_rec("user", "<local-command-stdout>ok</local-command-stdout>", "2026-09-12T14:45:02.000Z"))
        fh.write(_rec("user", "<system-reminder>hooks fired</system-reminder>", "2026-09-12T14:45:03.000Z"))
        fh.write(_rec("user", "[Request interrupted by user]", "2026-09-12T14:45:04.000Z"))
        fh.write(_rec("user", "   ", "2026-09-12T14:45:05.000Z"))
        fh.write(_rec("assistant", "still working", "2026-09-12T14:45:06.000Z"))

    assert detector.sweep([_session(path)], vault_root=tmp_path) == 0
    assert _entry(tmp_path)["unblocks"] == []


def test_a_cross_session_message_records_its_body(tmp_path: Path) -> None:
    """``SendMessage`` is how a maintainer's session answers a blocked child;
    it arrives wrapped, and the wrapper is not the answer."""
    path = _transcript(tmp_path, _OPENING, _QUESTION)
    detector.sweep([_session(path)], vault_root=tmp_path)

    wrapped = (
        "Another Claude session sent a message:\n"
        '<cross-session-message from="uds:/tmp/cc-socks/1.sock" from-name="mnemo-43" from-mode="prompting">\n'
        "Português. Regra para o futuro: abra com um glossário.\n"
        "</cross-session-message>\n\n"
        "This came from another Claude session — not typed by your user."
    )
    with path.open("a", encoding="utf-8") as fh:
        fh.write(_rec("user", wrapped, "2026-09-12T14:46:25.309Z", isMeta=True))

    assert detector.sweep([_session(path)], vault_root=tmp_path) == 1
    (unblock,) = _entry(tmp_path)["unblocks"]
    assert unblock["answer"] == "Português. Regra para o futuro: abra com um glossário."


def test_a_recorded_turn_is_recorded_once(tmp_path: Path) -> None:
    path = _transcript(tmp_path, _OPENING, _QUESTION)
    detector.sweep([_session(path)], vault_root=tmp_path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(_ANSWER)

    assert detector.sweep([_session(path)], vault_root=tmp_path) == 1
    assert detector.sweep([_session(path)], vault_root=tmp_path) == 0
    assert detector.sweep([_session(path)], vault_root=tmp_path) == 0
    assert len(_entry(tmp_path)["unblocks"]) == 1


def test_two_turns_in_one_region_are_two_markers(tmp_path: Path) -> None:
    path = _transcript(tmp_path, _OPENING, _QUESTION)
    detector.sweep([_session(path)], vault_root=tmp_path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(_ANSWER + _FOLLOW_UP)
        fh.write(_rec("user", "yes, badges too", "2026-09-12T14:47:00.000Z"))

    assert detector.sweep([_session(path)], vault_root=tmp_path) == 2

    first, second = _entry(tmp_path)["unblocks"]
    assert first["needs"] == "Which language should the summary be in?"
    assert second["needs"] == "Done. One more question: include the badges?"
    assert second["answer"] == "yes, badges too"


def test_a_partial_trailing_line_waits_for_the_next_sweep(tmp_path: Path) -> None:
    """The child may be mid-write. Half a record is neither an edge nor an
    error, and the bookmark must not step over it."""
    path = _transcript(tmp_path, _OPENING, _QUESTION)
    detector.sweep([_session(path)], vault_root=tmp_path)
    before = _entry(tmp_path)["offset"]

    head, tail = _ANSWER[:40], _ANSWER[40:]
    with path.open("a", encoding="utf-8") as fh:
        fh.write(head)
    assert detector.sweep([_session(path)], vault_root=tmp_path) == 0
    assert _entry(tmp_path)["offset"] == before

    with path.open("a", encoding="utf-8") as fh:
        fh.write(tail)
    assert detector.sweep([_session(path)], vault_root=tmp_path) == 1


def test_a_shrunken_transcript_re_baselines(tmp_path: Path) -> None:
    """A rotated or truncated file: the bookmark points past the end. Start
    over from the new end rather than replay the last window as new turns."""
    path = _transcript(tmp_path, _OPENING, _QUESTION, _ANSWER, _FOLLOW_UP)
    detector.sweep([_session(path)], vault_root=tmp_path)

    path.write_text(_OPENING, encoding="utf-8")
    assert detector.sweep([_session(path)], vault_root=tmp_path) == 0
    assert _entry(tmp_path)["offset"] == path.stat().st_size
    assert _entry(tmp_path)["unblocks"] == []


def test_a_new_transcript_path_re_baselines(tmp_path: Path) -> None:
    """A resumed session points ``linkScanPath`` at a new file that carries a
    copy of the old history. Treat it as a first sighting, not as growth."""
    old = _transcript(tmp_path, _OPENING, _QUESTION)
    detector.sweep([_session(old)], vault_root=tmp_path)

    new = _transcript(tmp_path, _OPENING, _QUESTION, _ANSWER, _FOLLOW_UP, name="sid-1b.jsonl")
    assert detector.sweep([_session(new)], vault_root=tmp_path) == 0
    assert _entry(tmp_path)["transcript"] == str(new)
    assert _entry(tmp_path)["offset"] == new.stat().st_size


def test_a_tempo_flip_with_no_new_human_turn_records_nothing(tmp_path: Path) -> None:
    """With a readable transcript the edge is read from it, never from
    ``tempo``: an ``active`` sighting on its own is not an answer, and
    counting both would double every edge the sweep does catch live."""
    path = _transcript(tmp_path, _OPENING, _QUESTION)
    detector.sweep([_session(path, "blocked")], vault_root=tmp_path)

    assert detector.sweep([_session(path, "active")], vault_root=tmp_path) == 0
    assert _entry(tmp_path)["unblocks"] == []
    assert _entry(tmp_path)["last_tempo"] == "active"


def test_an_unreadable_transcript_falls_back_to_the_tempo_edge(tmp_path: Path) -> None:
    """``state.json`` names a file that is not there: the old comparison is
    all that is left, and it is better than recording nothing."""
    missing = tmp_path / "transcripts" / "gone.jsonl"
    detector.sweep([_session(missing, "blocked", needs="which language?")], vault_root=tmp_path)

    assert detector.sweep([_session(missing, "active")], vault_root=tmp_path) == 1
    (unblock,) = _entry(tmp_path)["unblocks"]
    assert unblock["needs"] == "which language?"
    assert "answered_at" not in unblock


def test_needs_falls_back_to_the_last_blocked_sighting(tmp_path: Path) -> None:
    """The question was asked before the bookmark, so the scanned region has
    no assistant text to quote; ``last_needs`` from ``state.json`` is the
    next best label."""
    tool_only = json.dumps({
        "type": "assistant", "timestamp": "2026-09-12T14:44:40.000Z",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "AskUserQuestion", "input": {"q": "language?"}}]},
    }, separators=(",", ":")) + "\n"
    path = _transcript(tmp_path, _OPENING, tool_only)
    detector.sweep([_session(path, "blocked", needs="which language?")], vault_root=tmp_path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(_ANSWER)

    detector.sweep([_session(path, "blocked", needs="which language?")], vault_root=tmp_path)
    (unblock,) = _entry(tmp_path)["unblocks"]
    assert unblock["needs"] == "which language?"


def test_a_long_answer_is_excerpted(tmp_path: Path) -> None:
    path = _transcript(tmp_path, _OPENING, _QUESTION)
    detector.sweep([_session(path)], vault_root=tmp_path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(_rec("user", "word " * 200, "2026-09-12T14:46:25.309Z"))

    detector.sweep([_session(path)], vault_root=tmp_path)
    (unblock,) = _entry(tmp_path)["unblocks"]
    assert len(unblock["answer"]) <= detector.EXCERPT_CHARS
    assert unblock["answer"].startswith("word word")


def test_an_untouched_transcript_does_not_rewrite_the_file(tmp_path: Path, monkeypatch) -> None:
    """The no-churn rule survives the transcript read: a still session on a
    2s ``--watch`` must not cost a write per tick."""
    path = _transcript(tmp_path, _OPENING, _QUESTION)
    detector.sweep([_session(path)], vault_root=tmp_path)

    written = _writes(monkeypatch)
    detector.sweep([_session(path)], vault_root=tmp_path)
    detector.sweep([_session(path)], vault_root=tmp_path)

    assert written == []


def test_transcript_growth_moves_the_bookmark_and_writes(tmp_path: Path, monkeypatch) -> None:
    """Growth with no human turn still advances the bookmark on disk; the
    alternative is re-parsing an ever-growing region on every sweep."""
    path = _transcript(tmp_path, _OPENING, _QUESTION)
    detector.sweep([_session(path)], vault_root=tmp_path)
    before = _entry(tmp_path)["offset"]

    written = _writes(monkeypatch)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(_rec("assistant", "reading", "2026-09-12T14:45:00.000Z"))
    assert detector.sweep([_session(path)], vault_root=tmp_path) == 0

    assert len(written) == 1
    assert _entry(tmp_path)["offset"] > before


def test_is_human_turn_is_the_rule_the_measurement_tool_uses() -> None:
    """One definition of "a human turn". The tool that measured #176 reads
    edges out of transcripts with the same predicate, so its numbers and the
    detector's markers cannot drift apart."""
    import ast

    tool = Path(__file__).resolve().parents[2] / "tools" / "measure_unblock_edges.py"
    tree = ast.parse(tool.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "mnemo.core.sessions.detector"
        for alias in node.names
    }
    assert "is_human_turn" in imported


def test_a_first_sighting_mid_write_bookmarks_the_line_boundary(tmp_path: Path) -> None:
    """Seen while the child is half-way through writing the answer: the
    bookmark must stop before the partial line, so the completed record is
    read whole on the next sweep instead of skipped as corrupt."""
    path = _transcript(tmp_path, _OPENING, _QUESTION, _ANSWER[:40])
    assert detector.sweep([_session(path)], vault_root=tmp_path) == 0
    assert _entry(tmp_path)["offset"] == len((_OPENING + _QUESTION).encode("utf-8"))

    with path.open("a", encoding="utf-8") as fh:
        fh.write(_ANSWER[40:])
    assert detector.sweep([_session(path)], vault_root=tmp_path) == 1
