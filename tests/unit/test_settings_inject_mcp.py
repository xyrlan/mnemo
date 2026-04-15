"""Tests for v0.5 MCP server registration in ~/.claude.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mnemo.install import settings as inj


# --- inject ---


def test_inject_mcp_server_into_empty_file(tmp_path: Path):
    p = tmp_path / ".claude.json"
    p.write_text("{}")
    inj.inject_mcp_servers(p)
    data = json.loads(p.read_text())
    assert "mnemo" in data["mcpServers"]
    assert data["mcpServers"]["mnemo"]["args"] == ["-m", "mnemo", "mcp-server"]
    # command points at the running interpreter
    assert data["mcpServers"]["mnemo"]["command"] == (sys.executable or "python3")


def test_inject_mcp_server_creates_file_if_missing(tmp_path: Path):
    p = tmp_path / ".claude.json"
    inj.inject_mcp_servers(p)
    assert p.exists()
    data = json.loads(p.read_text())
    assert "mnemo" in data["mcpServers"]


def test_inject_mcp_server_preserves_unrelated_keys(tmp_path: Path):
    p = tmp_path / ".claude.json"
    p.write_text(json.dumps({
        "theme": "dark",
        "permissions": {"allow": ["Bash"]},
        "mcpServers": {"other-server": {"command": "node", "args": ["other.js"]}},
    }))
    inj.inject_mcp_servers(p)
    data = json.loads(p.read_text())
    assert data["theme"] == "dark"
    assert data["permissions"] == {"allow": ["Bash"]}
    assert data["mcpServers"]["other-server"]["command"] == "node"
    assert "mnemo" in data["mcpServers"]


def test_inject_mcp_server_is_idempotent(tmp_path: Path):
    p = tmp_path / ".claude.json"
    p.write_text("{}")
    inj.inject_mcp_servers(p)
    inj.inject_mcp_servers(p)
    data = json.loads(p.read_text())
    # Still exactly one mnemo entry
    assert list(data["mcpServers"].keys()).count("mnemo") == 1


def test_inject_mcp_server_overwrites_stale_entry(tmp_path: Path):
    p = tmp_path / ".claude.json"
    p.write_text(json.dumps({
        "mcpServers": {
            "mnemo": {"command": "/old/python", "args": ["-m", "mnemo", "old-cmd"]},
        },
    }))
    inj.inject_mcp_servers(p)
    data = json.loads(p.read_text())
    assert data["mcpServers"]["mnemo"]["args"] == ["-m", "mnemo", "mcp-server"]
    assert data["mcpServers"]["mnemo"]["command"] != "/old/python"


def test_inject_mcp_server_writes_backup(tmp_path: Path):
    p = tmp_path / ".claude.json"
    p.write_text('{"theme": "dark"}')
    inj.inject_mcp_servers(p)
    backups = list(tmp_path.glob(".claude.json.bak.*"))
    assert len(backups) == 1


def test_inject_mcp_server_refuses_malformed_json(tmp_path: Path):
    p = tmp_path / ".claude.json"
    p.write_text("{not valid json")
    with pytest.raises(inj.SettingsError):
        inj.inject_mcp_servers(p)


# --- uninject ---


def test_uninject_mcp_server_removes_only_mnemo(tmp_path: Path):
    p = tmp_path / ".claude.json"
    p.write_text(json.dumps({
        "mcpServers": {
            "mnemo": {"command": "python", "args": ["-m", "mnemo", "mcp-server"]},
            "other-server": {"command": "node", "args": ["other.js"]},
        },
    }))
    inj.uninject_mcp_servers(p)
    data = json.loads(p.read_text())
    assert "mnemo" not in data["mcpServers"]
    assert "other-server" in data["mcpServers"]


def test_uninject_mcp_server_drops_empty_mcpServers_key(tmp_path: Path):
    p = tmp_path / ".claude.json"
    p.write_text(json.dumps({
        "theme": "dark",
        "mcpServers": {
            "mnemo": {"command": "python", "args": ["-m", "mnemo", "mcp-server"]},
        },
    }))
    inj.uninject_mcp_servers(p)
    data = json.loads(p.read_text())
    assert "mcpServers" not in data
    assert data["theme"] == "dark"


def test_uninject_mcp_server_noop_on_missing_file(tmp_path: Path):
    p = tmp_path / ".claude.json"
    inj.uninject_mcp_servers(p)  # must not raise
    assert not p.exists()


def test_uninject_mcp_server_noop_when_mnemo_absent(tmp_path: Path):
    p = tmp_path / ".claude.json"
    p.write_text(json.dumps({
        "mcpServers": {"other-server": {"command": "node", "args": ["x.js"]}},
    }))
    inj.uninject_mcp_servers(p)
    data = json.loads(p.read_text())
    assert "other-server" in data["mcpServers"]


def test_inject_uninject_round_trip(tmp_path: Path):
    p = tmp_path / ".claude.json"
    p.write_text('{"theme": "dark"}')
    inj.inject_mcp_servers(p)
    inj.uninject_mcp_servers(p)
    data = json.loads(p.read_text())
    assert data == {"theme": "dark"}
