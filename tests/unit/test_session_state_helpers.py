"""CRUD helpers around injected_cache and session_emissions."""
from __future__ import annotations

import json
from datetime import date

from mnemo.core.mcp import session_state


def _seed(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_add_injection_records_slug_and_preserves_count(tmp_vault):
    path = tmp_vault / ".mnemo" / "mcp-call-counter.json"
    _seed(path, {"date": date.today().isoformat(), "count": 7})

    session_state.add_injection(tmp_vault, slug="use-prisma-mock", sid="sid-abc", now_ts=1000)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["count"] == 7
    assert data["injected_cache"]["use-prisma-mock"] == 1000


def test_read_injected_cache_returns_empty_on_fresh_file(tmp_vault):
    assert session_state.read_injected_cache(tmp_vault) == {}


def test_bump_emission_creates_and_increments(tmp_vault):
    session_state.bump_emission(tmp_vault, sid="sid-xyz", kind="reflex", now_ts=500)
    session_state.bump_emission(tmp_vault, sid="sid-xyz", kind="reflex", now_ts=600)
    session_state.bump_emission(tmp_vault, sid="sid-xyz", kind="enrich", now_ts=700)

    path = tmp_vault / ".mnemo" / "mcp-call-counter.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    e = data["session_emissions"]["sid-xyz"]
    assert e["reflex_count"] == 2
    assert e["enrich_count"] == 1
    assert e["started_at"] == 500  # first bump sets started_at; later ones don't move it


def test_gc_old_sessions_removes_entries_older_than_24h(tmp_vault):
    now = 1_000_000_000
    stale_started = now - (25 * 3600)
    fresh_started = now - 600
    path = tmp_vault / ".mnemo" / "mcp-call-counter.json"
    _seed(path, {
        "date": date.today().isoformat(),
        "count": 0,
        "injected_cache": {"a": 1, "b": 2},
        "session_emissions": {
            "stale": {"started_at": stale_started, "reflex_count": 1, "enrich_count": 0},
            "fresh": {"started_at": fresh_started, "reflex_count": 2, "enrich_count": 0},
        },
    })

    session_state.gc_old_sessions(tmp_vault, now_ts=now, ttl_seconds=24 * 3600)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert list(data["session_emissions"]) == ["fresh"]
