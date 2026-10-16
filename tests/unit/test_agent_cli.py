from __future__ import annotations

import pytest

from mnemo.core import agent_cli


def test_nothing_set_is_claude():
    assert agent_cli.current_cli({}) == "claude"


def test_mnemo_agent_names_the_cli():
    assert agent_cli.current_cli({"MNEMO_AGENT": "grok"}) == "grok"
    assert agent_cli.current_cli({"MNEMO_AGENT": "claude"}) == "claude"


def test_inside_a_grok_session_is_grok():
    assert agent_cli.current_cli({"GROK_SESSION_ID": "01a1-x"}) == "grok"


def test_grok_session_wins_over_inherited_claude_variables():
    env = {"GROK_SESSION_ID": "01a1-x", "CLAUDE_CODE_SESSION_ID": "abc"}
    assert agent_cli.current_cli(env) == "grok"


def test_explicit_mnemo_agent_wins_over_grok_session():
    env = {"MNEMO_AGENT": "claude", "GROK_SESSION_ID": "01a1-x"}
    assert agent_cli.current_cli(env) == "claude"


def test_override_wins_over_everything():
    env = {"MNEMO_AGENT": "claude", "GROK_SESSION_ID": "01a1-x"}
    assert agent_cli.current_cli(env, override="grok") == "grok"


def test_empty_values_count_as_unset():
    assert agent_cli.current_cli({"MNEMO_AGENT": " ", "GROK_SESSION_ID": ""}) == "claude"


def test_unknown_name_raises_instead_of_falling_back():
    with pytest.raises(agent_cli.UnknownAgentError):
        agent_cli.current_cli({"MNEMO_AGENT": "gork"})
    with pytest.raises(agent_cli.UnknownAgentError):
        agent_cli.current_cli({}, override="codex")


def test_reads_os_environ_when_no_mapping_given(monkeypatch):
    monkeypatch.setenv("MNEMO_AGENT", "grok")
    assert agent_cli.current_cli() == "grok"
