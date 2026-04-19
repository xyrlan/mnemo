"""Tests for the reflex ⚡ segment in the mnemo statusline."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo import statusline as sl
from mnemo.core.mcp import session_state


def _write_claude_json_with_mnemo(path: Path) -> None:
    path.write_text(json.dumps({
        "mcpServers": {
            "mnemo": {"command": "python", "args": ["-m", "mnemo", "mcp-server"]},
        },
    }))


@pytest.fixture(autouse=True)
def _no_project_resolution(monkeypatch):
    """Statusline tests expect vault-wide counts unless overridden."""
    monkeypatch.setattr(
        "mnemo.core.agent.resolve_agent",
        lambda cwd: type("A", (), {"name": None, "repo_root": cwd, "has_git": False})(),
    )


def test_statusline_emits_reflex_segment_when_reflex_count_nonzero(tmp_vault, tmp_path):
    claude_json = tmp_path / ".claude.json"
    _write_claude_json_with_mnemo(claude_json)
    session_state.bump_emission(tmp_vault, sid="s1", kind="reflex", now_ts=1)
    session_state.bump_emission(tmp_vault, sid="s1", kind="reflex", now_ts=2)
    session_state.bump_emission(tmp_vault, sid="s1", kind="reflex", now_ts=3)

    rendered = sl.render(tmp_vault, claude_json, cwd=str(tmp_vault))

    assert "3\u26a1" in rendered
    assert "today" not in rendered  # style consistency: no "today" suffix


def test_statusline_omits_reflex_segment_when_zero(tmp_vault, tmp_path):
    claude_json = tmp_path / ".claude.json"
    _write_claude_json_with_mnemo(claude_json)

    rendered = sl.render(tmp_vault, claude_json, cwd=str(tmp_vault))

    assert "\u26a1" not in rendered


def test_statusline_reflex_segment_aggregates_across_sessions(tmp_vault, tmp_path):
    """Multiple sessions' reflex counts should sum into the single ⚡ display."""
    claude_json = tmp_path / ".claude.json"
    _write_claude_json_with_mnemo(claude_json)
    session_state.bump_emission(tmp_vault, sid="s1", kind="reflex", now_ts=1)
    session_state.bump_emission(tmp_vault, sid="s1", kind="reflex", now_ts=2)
    session_state.bump_emission(tmp_vault, sid="s2", kind="reflex", now_ts=3)
    # enrich counts must NOT inflate the reflex segment.
    session_state.bump_emission(tmp_vault, sid="s1", kind="enrich", now_ts=4)

    rendered = sl.render(tmp_vault, claude_json, cwd=str(tmp_vault))

    assert "3\u26a1" in rendered
