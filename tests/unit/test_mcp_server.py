"""Tests for the mnemo MCP stdio server (JSON-RPC dispatcher + serve loop)."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from mnemo.core.mcp.server import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    handle_request,
    serve,
)


def _write_page(
    vault: Path,
    page_type: str,
    slug: str,
    *,
    tags: list[str],
    sources: list[str],
    body: str = "the rule body\n",
) -> None:
    target_dir = vault / "shared" / page_type
    target_dir.mkdir(parents=True, exist_ok=True)
    sources_yaml = "\n".join(f"  - {s}" for s in sources)
    tags_yaml = "\n".join(f"  - {t}" for t in tags)
    (target_dir / f"{slug}.md").write_text(
        "---\n"
        f"name: {slug}\n"
        f"description: d\n"
        f"type: {page_type}\n"
        f"stability: stable\n"
        "sources:\n"
        f"{sources_yaml}\n"
        "tags:\n"
        f"{tags_yaml}\n"
        "---\n\n"
        f"{body}"
    )


# --- initialize / capabilities ---


def test_initialize_returns_protocol_version_and_caps():
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}},
    }
    resp = handle_request(req, vault_root=None)
    assert resp is not None
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert "tools" in resp["result"]["capabilities"]
    assert resp["result"]["serverInfo"]["name"] == SERVER_NAME
    assert "version" in resp["result"]["serverInfo"]


def test_notifications_initialized_returns_none():
    """Notifications must not be replied to."""
    req = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert handle_request(req, vault_root=None) is None


# --- tools/list ---


def test_tools_list_returns_three_tools():
    req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    resp = handle_request(req, vault_root=None)
    assert resp is not None
    names = [t["name"] for t in resp["result"]["tools"]]
    assert sorted(names) == sorted([
        "list_rules_by_topic",
        "read_mnemo_rule",
        "get_mnemo_topics",
    ])


def test_tools_list_each_tool_has_input_schema():
    req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    resp = handle_request(req, vault_root=None)
    for tool in resp["result"]["tools"]:
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool
        assert tool["inputSchema"]["type"] == "object"


# --- tools/call ---


def test_tools_call_list_rules_by_topic_round_trip(tmp_vault):
    _write_page(
        tmp_vault, "feedback", "use-yarn",
        tags=["auto-promoted", "package-management"],
        sources=["bots/a/m.md", "bots/b/m.md"],
    )
    req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "list_rules_by_topic",
            "arguments": {"topic": "package-management"},
        },
    }
    resp = handle_request(req, vault_root=tmp_vault)
    assert resp is not None
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload == [
        {"slug": "use-yarn", "type": "feedback", "source_count": 2}
    ]


def test_tools_call_read_mnemo_rule_returns_body(tmp_vault):
    _write_page(
        tmp_vault, "feedback", "use-yarn",
        tags=["auto-promoted", "package-management"],
        sources=["bots/a/m.md"],
        body="Always use yarn.\n",
    )
    req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "read_mnemo_rule", "arguments": {"slug": "use-yarn"}},
    }
    resp = handle_request(req, vault_root=tmp_vault)
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["slug"] == "use-yarn"
    assert payload["body"] == "Always use yarn.\n"


def test_tools_call_read_mnemo_rule_unknown_slug_returns_null(tmp_vault):
    req = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "read_mnemo_rule", "arguments": {"slug": "ghost"}},
    }
    resp = handle_request(req, vault_root=tmp_vault)
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload is None


def test_tools_call_get_mnemo_topics_returns_sorted_list(tmp_vault):
    _write_page(
        tmp_vault, "feedback", "f1",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],
    )
    _write_page(
        tmp_vault, "user", "u1",
        tags=["auto-promoted", "typing"],
        sources=["bots/a/m.md"],
    )
    req = {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {"name": "get_mnemo_topics", "arguments": {}},
    }
    resp = handle_request(req, vault_root=tmp_vault)
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload == ["git", "typing"]


def test_tools_call_unknown_tool_returns_neg_32602(tmp_vault):
    req = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "nope", "arguments": {}},
    }
    resp = handle_request(req, vault_root=tmp_vault)
    assert resp["error"]["code"] == -32602
    assert "nope" in resp["error"]["message"]


def test_tools_call_without_vault_returns_neg_32603():
    req = {
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {"name": "get_mnemo_topics", "arguments": {}},
    }
    resp = handle_request(req, vault_root=None)
    assert resp["error"]["code"] == -32603


# --- error paths ---


def test_unknown_method_returns_neg_32601():
    req = {"jsonrpc": "2.0", "id": 9, "method": "wat/no"}
    resp = handle_request(req, vault_root=None)
    assert resp["error"]["code"] == -32601
    assert "wat/no" in resp["error"]["message"]


def test_unknown_notification_is_silently_dropped():
    req = {"jsonrpc": "2.0", "method": "wat/no"}  # no id → notification
    assert handle_request(req, vault_root=None) is None


# --- serve loop ---


def test_serve_loop_processes_two_requests(tmp_vault, monkeypatch):
    # Force serve() to use tmp_vault as the resolved vault root.
    monkeypatch.setattr(
        "mnemo.core.config.load_config",
        lambda: {"vaultRoot": str(tmp_vault)},
    )
    monkeypatch.setattr(
        "mnemo.core.paths.vault_root",
        lambda cfg: tmp_vault,
    )

    _write_page(
        tmp_vault, "feedback", "f1",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],
    )

    req1 = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    req2 = json.dumps({
        "jsonrpc": "2.0", "id": 2,
        "method": "tools/call",
        "params": {"name": "get_mnemo_topics", "arguments": {}},
    })
    stdin = io.StringIO(req1 + "\n" + req2 + "\n")
    stdout = io.StringIO()

    rc = serve(stdin=stdin, stdout=stdout)
    assert rc == 0
    lines = [ln for ln in stdout.getvalue().splitlines() if ln]
    assert len(lines) == 2
    parsed = [json.loads(ln) for ln in lines]
    assert parsed[0]["id"] == 1
    assert parsed[0]["result"]["serverInfo"]["name"] == "mnemo"
    assert parsed[1]["id"] == 2
    payload = json.loads(parsed[1]["result"]["content"][0]["text"])
    assert payload == ["git"]


def test_serve_loop_skips_blank_lines_and_garbage(tmp_vault, monkeypatch):
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {})
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: tmp_vault)

    stdin = io.StringIO(
        "\n"
        "not json at all\n"
        "[1, 2, 3]\n"  # JSON but not a dict
        '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}\n'
    )
    stdout = io.StringIO()
    serve(stdin=stdin, stdout=stdout)
    lines = [ln for ln in stdout.getvalue().splitlines() if ln]
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == 1


def test_serve_loop_drops_notification_responses(tmp_vault, monkeypatch):
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {})
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: tmp_vault)

    stdin = io.StringIO(
        '{"jsonrpc": "2.0", "method": "notifications/initialized"}\n'
        '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}\n'
    )
    stdout = io.StringIO()
    serve(stdin=stdin, stdout=stdout)
    lines = [ln for ln in stdout.getvalue().splitlines() if ln]
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == 1


def test_tools_call_increments_counter(tmp_vault):
    """Each successful tools/call must bump the daily counter."""
    from mnemo.core.mcp import counter as mcp_counter

    _write_page(
        tmp_vault, "feedback", "f1",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],
    )
    assert mcp_counter.read_today(tmp_vault) == 0

    req = {
        "jsonrpc": "2.0",
        "id": 100,
        "method": "tools/call",
        "params": {"name": "get_mnemo_topics", "arguments": {}},
    }
    handle_request(req, vault_root=tmp_vault)
    assert mcp_counter.read_today(tmp_vault) == 1

    handle_request(req, vault_root=tmp_vault)
    handle_request(req, vault_root=tmp_vault)
    assert mcp_counter.read_today(tmp_vault) == 3


def test_tools_call_unknown_tool_does_not_increment(tmp_vault):
    """A failed tool call should not bump the counter."""
    from mnemo.core.mcp import counter as mcp_counter

    req = {
        "jsonrpc": "2.0",
        "id": 200,
        "method": "tools/call",
        "params": {"name": "no-such-tool", "arguments": {}},
    }
    handle_request(req, vault_root=tmp_vault)
    assert mcp_counter.read_today(tmp_vault) == 0


def test_serve_loop_survives_load_config_failure(tmp_vault, monkeypatch):
    """If config load explodes, serve should still answer with -32603 on tools/call."""
    def boom():
        raise RuntimeError("config gone")
    monkeypatch.setattr("mnemo.core.config.load_config", boom)

    stdin = io.StringIO(
        '{"jsonrpc": "2.0", "id": 1, "method": "tools/call", '
        '"params": {"name": "get_mnemo_topics", "arguments": {}}}\n'
    )
    stdout = io.StringIO()
    serve(stdin=stdin, stdout=stdout)
    line = stdout.getvalue().strip()
    parsed = json.loads(line)
    assert parsed["error"]["code"] == -32603
