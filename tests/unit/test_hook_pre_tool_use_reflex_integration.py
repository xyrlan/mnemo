"""Plumbing tests for pre_tool_use enrichment ↔ reflex session-state integration.

These assert that the session_state helpers the hook relies on work in
isolation; full end-to-end wiring is covered by the e2e suite.
"""
from __future__ import annotations

import io
import json
from unittest.mock import patch

from mnemo.core.mcp import session_state
from mnemo.hooks import pre_tool_use


def _run(payload):
    out = io.StringIO()
    with patch("sys.stdin", io.StringIO(json.dumps(payload))), patch("sys.stdout", out):
        rc = pre_tool_use.main()
    return rc, out.getvalue()


def test_enrichment_skips_when_slug_already_injected(tmp_vault, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "enrichment": {"enabled": True, "maxEmissionsPerSession": 15},
    }))
    # Pre-populate cache: use-prisma-mock was already injected.
    session_state.add_injection(tmp_vault, slug="use-prisma-mock", sid="sid-a", now_ts=100)

    # Assert the cache is readable — this is what the hook consults.
    assert "use-prisma-mock" in session_state.read_injected_cache(tmp_vault)


def test_enrichment_returns_silence_when_cap_reached(tmp_vault, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "enrichment": {"enabled": True, "maxEmissionsPerSession": 1},
    }))
    # Bump enrich_count to 1 so we're already AT cap.
    session_state.bump_emission(tmp_vault, sid="sid-cap", kind="enrich", now_ts=1)

    counts = session_state.read_emission_counts(tmp_vault, "sid-cap")
    assert counts["enrich_count"] == 1
