"""``--json`` carries the derived answer, not just the raw fields (#222).

A JSON consumer asks the same question the renderer asks: is this session
waiting on me, is it finished. ``Session`` answers both correctly, in one
place, as ``@property`` — and ``asdict()`` cannot see a property, so every
consumer re-implemented the rule and the watcher on the 2026-09-13 dispatch
got it wrong in a way that stayed silent across three children.

``is_waiting`` is the one that cannot be re-derived by eye: it is a three-way
interaction between ``tempo`` and ``live`` that exists because of #196.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from mnemo.cli.commands import sessions as sessions_cmd
from mnemo.core.sessions.jobs import Session

DERIVED = ("is_waiting", "is_blocked", "is_done", "is_abandoned", "is_stale")


def _emit(monkeypatch, found: list[Session], capsys) -> list[dict]:
    monkeypatch.setattr(
        "mnemo.core.sessions.jobs.read_sessions",
        lambda root=None, *, cwd=None: found,
    )
    monkeypatch.setattr("mnemo.core.sessions.detector.sweep",
                        lambda sessions, *, vault_root: 0)
    args = argparse.Namespace(json=True, watch=False, **{"all": True})
    assert sessions_cmd.cmd_sessions(args) == 0
    return json.loads(capsys.readouterr().out)


def test_every_derived_boolean_reaches_json(monkeypatch, capsys) -> None:
    """The four properties the renderer branches on, emitted as fields."""
    s = Session(short_id="a", state="working", tempo="blocked", live=True)

    (row,) = _emit(monkeypatch, [s], capsys)

    for key in DERIVED:
        assert key in row, f"{key} missing from --json"
        assert isinstance(row[key], bool)


def test_json_agrees_with_the_properties(monkeypatch, capsys) -> None:
    """One rule, two surfaces. A drift here is the bug #222 reported."""
    found = [
        Session(short_id="wait", state="blocked", tempo="blocked", live=True),
        Session(short_id="dead", state="blocked", tempo="blocked", live=False),
        Session(short_id="done", state="done", tempo="idle", live=None),
        Session(short_id="work", state="working", tempo="active", live=True),
        Session(short_id="stop", state="stopped", tempo="idle", live=False),
    ]

    rows = {r["short_id"]: r for r in _emit(monkeypatch, found, capsys)}

    for s in found:
        row = rows[s.short_id]
        for key in DERIVED:
            assert row[key] is getattr(s, key), f"{s.short_id}.{key} drifted"


def test_the_raw_fields_are_still_there(monkeypatch, capsys) -> None:
    """Additive only: an existing consumer reading state/tempo/live is intact."""
    s = Session(short_id="a", state="blocked", tempo="blocked", live=False,
                needs="q?", name="n", cwd="/repo", tokens=7)

    (row,) = _emit(monkeypatch, [s], capsys)

    assert row["state"] == "blocked"
    assert row["tempo"] == "blocked"
    assert row["live"] is False
    assert row["needs"] == "q?"
    assert row["tokens"] == 7


def test_the_filter_the_watcher_should_have_written(monkeypatch, capsys) -> None:
    """The whole point: one key answers the question, no re-derivation.

    The watcher that failed wrote ``tempo == "blocked" or state in
    ("done", "stopped") or live is False``. Against real data that both
    over-fires (an abandoned session is not a live request) and reads fields
    whose range it guessed at.
    """
    found = [
        Session(short_id="wait", state="blocked", tempo="blocked", live=True),
        Session(short_id="dead", state="blocked", tempo="blocked", live=False),
        Session(short_id="work", state="working", tempo="active", live=True),
    ]

    rows = _emit(monkeypatch, found, capsys)

    assert [r["short_id"] for r in rows if r["is_waiting"]] == ["wait"]
    assert [r["short_id"] for r in rows if r["is_abandoned"]] == ["dead"]


# --- the `stopped` phase ---------------------------------------------------

@pytest.mark.parametrize("state", ["done", "stopped"])
def test_a_finished_session_is_done(state: str) -> None:
    """``stopped`` is a terminal phase and was filed under "working".

    Measured on nine real sessions, 2026-09-13: ``state`` carries ``stopped``
    for a session whose process ended without finishing its turn. ``is_done``
    tested ``== "done"`` alone, so two real sessions rendered under
    TRABALHANDO with the literal word ``stopped`` as their activity — and a
    consumer polling for completion never saw them end.
    """
    assert Session(short_id="a", state=state, tempo="idle").is_done is True


@pytest.mark.parametrize("state", ["working", None])
def test_a_running_session_is_not_done(state: str | None) -> None:
    assert Session(short_id="a", state=state, tempo="active").is_done is False


def test_a_blocked_session_is_not_done_even_when_stopped() -> None:
    """Waiting outranks finished: the maintainer's claim is what matters."""
    s = Session(short_id="a", state="stopped", tempo="blocked", live=True)

    assert s.is_waiting is True


def test_stopped_sessions_render_as_finished() -> None:
    """The bucket the two real sessions belonged in all along."""
    from mnemo.core.sessions.render import render_queue

    out = render_queue([
        Session(short_id="stop", state="stopped", tempo="idle",
                name="s", updated_at="2026-09-13T16:37:45.000Z"),
    ])

    assert "TRABALHANDO" not in out
    assert "PRONTAS" in out
