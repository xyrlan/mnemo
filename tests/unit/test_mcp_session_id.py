"""The session an MCP tool call belongs to, on its access-log row (#416).

Claude Code spawns the server once per process and exports the session it was
spawned for; ``/clear`` then starts a new session in the same process and the
environment keeps the old id (``mcp-server-session`` in
:mod:`mnemo.core.claude_cli`). What is pinned: the live id wins when the
sessions file vouches for the socket the server was given, the spawn id is the
answer on every gap, nothing here raises, and every tool row carries the key.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core import claude_cli
from mnemo.core.mcp import server

SPAWNED = "11111111-1111-4111-8111-111111111111"
LIVE = "22222222-2222-4222-8222-222222222222"
SOCKET = "/tmp/cc-socks/4242.sock"


def _sessions_file(home: Path, pid: str, body) -> Path:
    path = home / ".claude" / "sessions" / f"{pid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    return path


def _env(**extra):
    env = {server.SESSION_ENV: SPAWNED, server.SOCKET_ENV: SOCKET}
    env.update(extra)
    return env


def test_outside_claude_code_there_is_no_session():
    assert server.session_id({}) is None
    assert server.session_id({server.SESSION_ENV: "  "}) is None


def test_after_a_clear_the_sessions_file_beats_the_spawn_environment(tmp_home):
    _sessions_file(tmp_home, "4242", {"pid": 4242, "sessionId": LIVE,
                                      "messagingSocketPath": SOCKET})
    assert server.session_id(_env()) == LIVE


def test_without_a_socket_the_spawn_id_is_the_answer(tmp_home):
    _sessions_file(tmp_home, "4242", {"sessionId": LIVE, "messagingSocketPath": SOCKET})
    assert server.session_id({server.SESSION_ENV: SPAWNED}) == SPAWNED


@pytest.mark.parametrize("body", [
    {"sessionId": LIVE, "messagingSocketPath": "/tmp/cc-socks/9999.sock"},  # someone else's
    {"sessionId": LIVE},                                                    # vouches for nothing
    {"sessionId": "", "messagingSocketPath": SOCKET},
    {"sessionId": 7, "messagingSocketPath": SOCKET},
    ["not", "an", "object"],
    "{torn",
])
def test_a_sessions_file_that_does_not_vouch_for_the_socket_is_ignored(tmp_home, body):
    _sessions_file(tmp_home, "4242", body)
    assert server.session_id(_env()) == SPAWNED


def test_a_missing_sessions_file_or_a_socket_not_named_by_a_pid_falls_back(tmp_home):
    assert server.session_id(_env()) == SPAWNED
    pipe = r"\\.\pipe\claude-4242"
    _sessions_file(tmp_home, "4242", {"sessionId": LIVE, "messagingSocketPath": pipe})
    assert server.session_id(_env(**{server.SOCKET_ENV: pipe})) == SPAWNED


def test_the_assumption_it_rests_on_is_registered():
    assert "session_id" in claude_cli.assumption("mcp-server-session").used_by


def _log(vault: Path):
    path = vault / ".mnemo" / "mcp-access-log.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.parametrize("tool,arguments", [
    ("list_rules_by_topic", {"topic": "git", "query": "q"}),
    ("read_mnemo_rule", {"slug": "nothing-here"}),
    ("get_mnemo_topics", {}),
])
def test_every_tool_row_carries_the_session(tmp_vault, tmp_home, monkeypatch, tool, arguments):
    monkeypatch.setattr("mnemo.core.mcp.tools._resolve_current_project", lambda vault_root: None)
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": tool, "arguments": arguments}}

    monkeypatch.delenv(server.SESSION_ENV, raising=False)
    monkeypatch.delenv(server.SOCKET_ENV, raising=False)
    server.handle_request(request, vault_root=tmp_vault)
    assert _log(tmp_vault)[-1]["session_id"] is None

    monkeypatch.setenv(server.SESSION_ENV, SPAWNED)
    monkeypatch.setenv(server.SOCKET_ENV, SOCKET)
    _sessions_file(tmp_home, "4242", {"sessionId": LIVE, "messagingSocketPath": SOCKET})
    server.handle_request(request, vault_root=tmp_vault)
    assert _log(tmp_vault)[-1]["session_id"] == LIVE
