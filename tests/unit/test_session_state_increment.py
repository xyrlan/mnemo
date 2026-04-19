"""increment() must preserve unknown top-level keys (v0.8 contract)."""
from __future__ import annotations

import json
from datetime import date

from mnemo.core.mcp import session_state


def test_increment_preserves_injected_cache(tmp_vault):
    path = tmp_vault / ".mnemo" / "mcp-call-counter.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    seed = {
        "date": today,
        "count": 4,
        "injected_cache": {"use-prisma-mock": 1713456000},
        "session_emissions": {"sid-abc": {"started_at": 1, "reflex_count": 2, "enrich_count": 0}},
    }
    path.write_text(json.dumps(seed), encoding="utf-8")

    session_state.increment(tmp_vault)

    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded["count"] == 5
    assert reloaded["injected_cache"] == seed["injected_cache"]
    assert reloaded["session_emissions"] == seed["session_emissions"]


def test_increment_day_rollover_wipes_new_keys_too(tmp_vault):
    path = tmp_vault / ".mnemo" / "mcp-call-counter.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    seed = {
        "date": "1999-01-01",  # stale
        "count": 99,
        "injected_cache": {"stale-slug": 1},
        "session_emissions": {"stale-sid": {"started_at": 1, "reflex_count": 1, "enrich_count": 0}},
    }
    path.write_text(json.dumps(seed), encoding="utf-8")

    session_state.increment(tmp_vault)

    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded["date"] == date.today().isoformat()
    assert reloaded["count"] == 1
    assert reloaded["injected_cache"] == {}
    assert reloaded["session_emissions"] == {}
