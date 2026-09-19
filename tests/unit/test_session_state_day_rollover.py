"""The day-scoped state rolls over on the first access of any kind (#374).

Before the fix only ``increment()`` — reached solely by an MCP tool call —
compared the stored date with today. So yesterday's ``injected_cache`` kept
suppressing rules until someone used an MCP tool, and that call then reset
the whole file, dropping every entry the hooks had written since midnight.
Both halves are covered here; the live symptom was one session being told
the same rule twice, at 10:37 and again right after the 11:00 MCP call.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from mnemo.core.mcp import session_state


def _seed_yesterday(vault: Path, payload: dict) -> Path:
    path = vault / ".mnemo" / "mcp-call-counter.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"date": (date.today() - timedelta(days=1)).isoformat()}
    data.update(payload)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_a_read_retires_yesterdays_cache_without_waiting_for_an_mcp_call(tmp_vault: Path):
    _seed_yesterday(tmp_vault, {"count": 99,
                                "injected_cache": {"sid-old": {"yesterday-rule": 111}}})

    assert session_state.read_injected_cache(tmp_vault, "sid-old") == {}


def test_todays_writes_survive_the_first_mcp_call(tmp_vault: Path):
    """The mid-day wipe: a hook wrote at 10:37, the 11:00 MCP call erased it."""
    path = _seed_yesterday(tmp_vault, {"count": 99,
                                       "injected_cache": {"sid-old": {"yesterday-rule": 111}}})

    session_state.add_injection(tmp_vault, slug="today-rule", sid="sid-new", now_ts=1789644244)
    assert json.loads(path.read_text(encoding="utf-8"))["date"] == date.today().isoformat()

    session_state.increment(tmp_vault)

    assert session_state.read_injected_cache(tmp_vault, "sid-new") == {"today-rule": 1789644244}
    assert session_state.read_injected_cache(tmp_vault, "sid-old") == {}


def test_the_counter_still_restarts_at_one_on_the_new_day(tmp_vault: Path):
    """Whoever rolls the file over, the day's first MCP call is call 1."""
    _seed_yesterday(tmp_vault, {"count": 99})

    session_state.add_injection(tmp_vault, slug="a", sid="sid-1", now_ts=10)
    assert session_state.read_today(tmp_vault) == 0

    session_state.increment(tmp_vault)
    assert session_state.read_today(tmp_vault) == 1


def test_yesterdays_emissions_do_not_count_against_todays_session_cap(tmp_vault: Path):
    """``maxEmissionsPerSession`` is read per session; a resumed sid kept
    yesterday's tally and could start the day already capped."""
    _seed_yesterday(tmp_vault, {
        "count": 5,
        "session_emissions": {"sid-1": {"started_at": 1, "reflex_count": 10, "enrich_count": 2,
                                        "enriched": ["x"]}},
    })

    assert session_state.read_emission_counts(tmp_vault, "sid-1") == {
        "reflex_count": 0, "enrich_count": 0}
    assert session_state.read_enriched_slugs(tmp_vault, "sid-1") == set()
    assert session_state.read_today_emissions(tmp_vault) == 0


def test_rollover_keeps_top_level_keys_it_does_not_own(tmp_vault: Path):
    """A key written by another version has an unknown lifetime; dropping it
    is a guess, keeping it is not."""
    path = _seed_yesterday(tmp_vault, {"count": 3, "future_key": {"keep": "me"}})

    session_state.increment(tmp_vault)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["future_key"] == {"keep": "me"}
    assert data["count"] == 1


def test_increment_survives_a_corrupt_count(tmp_vault: Path):
    path = tmp_vault / ".mnemo" / "mcp-call-counter.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": date.today().isoformat(), "count": "not-a-number"}),
                    encoding="utf-8")

    session_state.increment(tmp_vault)

    assert session_state.read_today(tmp_vault) == 1
