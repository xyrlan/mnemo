"""End-to-end coverage for PreToolUse enrichment dedupe + session cap (v0.8).

Drives ``pre_tool_use.main()`` directly with a real rule + rule_activation
index. Verifies that the shared ``injected_cache`` + ``session_emissions``
counters actually gate emissions on the enrich path — not just on reflex.
"""
from __future__ import annotations

import io
import json
from unittest.mock import patch

from mnemo.hooks import pre_tool_use
from mnemo.core.mcp import session_state


def _run(payload):
    out = io.StringIO()
    with patch("sys.stdin", io.StringIO(json.dumps(payload))), patch("sys.stdout", out):
        rc = pre_tool_use.main()
    return rc, out.getvalue()


def _seed_activation_rule(vault):
    """Write a feedback rule with activates_on + build rule_activation index."""
    fb = vault / "shared" / "feedback"
    fb.mkdir(parents=True, exist_ok=True)
    (fb / "react-modal.md").write_text(
        "---\n"
        "name: react-modal\n"
        "description: Prefer HeroUI modal pattern\n"
        "tags:\n  - react\n"
        "sources:\n  - bots/projA/memory/a.md\n  - bots/projB/memory/b.md\n"
        "stability: stable\n"
        "activates_on:\n"
        "  tools:\n    - Edit\n    - Write\n"
        "  path_globs:\n    - \"**/modals/**\"\n"
        "---\n"
        "Use HeroUI for modals. Detail detail detail.\n",
        encoding="utf-8",
    )
    from mnemo.core import rule_activation
    rule_activation.write_index(vault, rule_activation.build_index(vault))


def test_enrichment_emits_then_dedupes_same_slug(tmp_vault, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "enrichment": {"enabled": True, "maxEmissionsPerSession": 15},
    }))
    _seed_activation_rule(tmp_vault)

    payload = {
        "cwd": str(tmp_vault),
        "session_id": "sid-e2e",
        "tool_name": "Edit",
        "tool_input": {"file_path": "src/modals/x.tsx"},
    }

    rc, out1 = _run(payload)
    assert rc == 0
    assert "react-modal" in out1

    # cache should now contain the slug
    assert "react-modal" in session_state.read_injected_cache(tmp_vault)

    # second invocation — same slug in cache → silent
    rc, out2 = _run(payload)
    assert rc == 0
    assert out2 == ""


def test_enrichment_silent_when_session_cap_reached(tmp_vault, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "enrichment": {"enabled": True, "maxEmissionsPerSession": 1},
    }))
    _seed_activation_rule(tmp_vault)
    # Prime the counter at cap
    session_state.bump_emission(tmp_vault, sid="sid-cap", kind="enrich", now_ts=1)

    payload = {
        "cwd": str(tmp_vault),
        "session_id": "sid-cap",
        "tool_name": "Edit",
        "tool_input": {"file_path": "src/modals/x.tsx"},
    }
    rc, out = _run(payload)
    assert rc == 0
    assert out == ""
