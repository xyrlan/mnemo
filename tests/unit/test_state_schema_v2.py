"""State schema v1 → v2 migration and round-trip tests."""
from __future__ import annotations

import json

from mnemo.core.extract import inbox
from mnemo.core.extract.inbox import StateSchemaError
from mnemo.core.extract.scanner import ExtractionState, StateEntry


def _write_v1_file(path, entries):
    payload = {
        "schema_version": 1,
        "last_run": "2026-04-10T12:00:00",
        "entries": entries,
    }
    path.write_text(json.dumps(payload))


def test_load_v1_state_migrates_last_sync_from_written_at(tmp_path):
    state_path = tmp_path / "state.json"
    _write_v1_file(state_path, {
        "feedback/use-yarn": {
            "source_files": ["bots/a/memory/feedback_use_yarn.md"],
            "source_hash": "sha256:aaa",
            "written_hash": "sha256:bbb",
            "written_at": "2026-04-10T12:00:00",
            "status": "inbox",
        },
    })

    state = inbox.load_state(state_path)

    assert state.schema_version == 2
    entry = state.entries["feedback/use-yarn"]
    assert entry.last_sync == "2026-04-10T12:00:00"
    assert entry.status == "inbox"


def test_load_v2_state_preserves_last_sync(tmp_path):
    state_path = tmp_path / "state.json"
    payload = {
        "schema_version": 2,
        "last_run": "2026-04-13T12:00:00",
        "entries": {
            "feedback/use-yarn": {
                "source_files": ["bots/a/memory/feedback_use_yarn.md"],
                "source_hash": "sha256:aaa",
                "written_hash": "sha256:bbb",
                "written_at": "2026-04-10T12:00:00",
                "last_sync": "2026-04-13T12:00:00",
                "status": "auto_promoted",
            },
        },
    }
    state_path.write_text(json.dumps(payload))

    state = inbox.load_state(state_path)

    assert state.schema_version == 2
    entry = state.entries["feedback/use-yarn"]
    assert entry.last_sync == "2026-04-13T12:00:00"
    assert entry.status == "auto_promoted"


def test_write_state_persists_v2_with_last_sync(tmp_path):
    state = ExtractionState(last_run="2026-04-13T12:00:00")
    state.entries["feedback/use-yarn"] = StateEntry(
        source_files=["bots/a/memory/feedback_use_yarn.md"],
        source_hash="sha256:aaa",
        written_hash="sha256:bbb",
        written_at="2026-04-13T12:00:00",
        status="auto_promoted",
        last_sync="2026-04-13T12:00:00",
    )

    state_path = tmp_path / "state.json"
    inbox.atomic_write_state(state, state_path)

    payload = json.loads(state_path.read_text())
    assert payload["schema_version"] == 2
    entry = payload["entries"]["feedback/use-yarn"]
    assert entry["last_sync"] == "2026-04-13T12:00:00"
    assert entry["status"] == "auto_promoted"


def test_v1_round_trip_through_load_and_save(tmp_path):
    state_path = tmp_path / "state.json"
    _write_v1_file(state_path, {
        "feedback/use-yarn": {
            "source_files": ["bots/a/memory/feedback_use_yarn.md"],
            "source_hash": "sha256:aaa",
            "written_hash": "sha256:bbb",
            "written_at": "2026-04-10T12:00:00",
            "status": "inbox",
        },
    })

    state = inbox.load_state(state_path)
    inbox.atomic_write_state(state, state_path)

    payload = json.loads(state_path.read_text())
    assert payload["schema_version"] == 2
    assert payload["entries"]["feedback/use-yarn"]["last_sync"] == "2026-04-10T12:00:00"


def test_unknown_schema_version_raises(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"schema_version": 99, "last_run": None, "entries": {}}))

    try:
        inbox.load_state(state_path)
    except StateSchemaError as exc:
        assert "99" in str(exc)
        return
    raise AssertionError("expected StateSchemaError")
