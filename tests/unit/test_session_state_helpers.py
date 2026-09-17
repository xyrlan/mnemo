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
    assert data["injected_cache"] == {"sid-abc": {"use-prisma-mock": 1000}}


def test_read_injected_cache_returns_empty_on_fresh_file(tmp_vault):
    assert session_state.read_injected_cache(tmp_vault, "sid-abc") == {}


def test_injected_cache_is_scoped_to_the_session_that_was_told(tmp_vault):
    session_state.add_injection(tmp_vault, slug="a", sid="sid-1", now_ts=10)
    session_state.add_injection(tmp_vault, slug="b", sid="sid-2", now_ts=20)

    assert session_state.read_injected_cache(tmp_vault, "sid-1") == {"a": 10}
    assert session_state.read_injected_cache(tmp_vault, "sid-2") == {"b": 20}
    assert session_state.read_injected_cache(tmp_vault, "sid-3") == {}


def test_enrichment_counts_as_told_only_for_its_own_session(tmp_vault):
    session_state.record_enrichment(tmp_vault, sid="sid-1", slugs=["x"], now_ts=5)

    assert session_state.read_injected_cache(tmp_vault, "sid-1") == {"x": 5}
    assert session_state.read_injected_cache(tmp_vault, "sid-2") == {}


def test_a_pre_361_flat_cache_is_dropped_not_misread(tmp_vault):
    """An older mnemo wrote ``{slug: ts}``. Those entries name no session:
    no session reads them as its own, and the next write keeps only the new
    shape (the day rollover would discard them anyway)."""
    path = tmp_vault / ".mnemo" / "mcp-call-counter.json"
    _seed(path, {"date": date.today().isoformat(), "count": 3,
                 "injected_cache": {"old-slug": 100}})

    assert session_state.read_injected_cache(tmp_vault, "old-slug") == {}
    assert session_state.read_injected_cache(tmp_vault, "sid-1") == {}

    session_state.add_injection(tmp_vault, slug="new", sid="sid-1", now_ts=200)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["injected_cache"] == {"sid-1": {"new": 200}}
    assert data["count"] == 3


def test_session_end_keeps_the_sessions_cache_for_a_resume(tmp_vault):
    """A resumed session still has the injected rules in its context."""
    session_state.add_injection(tmp_vault, slug="a", sid="sid-1", now_ts=10)
    session_state.bump_emission(tmp_vault, sid="sid-1", kind="reflex", now_ts=10)

    session_state.evict_session(tmp_vault, "sid-1")

    assert session_state.read_injected_cache(tmp_vault, "sid-1") == {"a": 10}
    assert session_state.read_emission_counts(tmp_vault, "sid-1")["reflex_count"] == 0


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
