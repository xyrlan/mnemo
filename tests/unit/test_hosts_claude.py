from __future__ import annotations

import json
from pathlib import Path


def test_claude_registers_in_claude_json_and_project_mcp_json(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host

    host = get_host("claude")
    r = host.register_mcp(project=False, cwd=tmp_path)
    assert Path(r.path) == tmp_home / ".claude.json" and r.method == "json"
    assert "mnemo" in json.loads((tmp_home / ".claude.json").read_text())["mcpServers"]

    r2 = host.register_mcp(project=True, cwd=tmp_path)
    assert Path(r2.path) == tmp_path / ".mcp.json"
    assert "mnemo" in json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]

    host.unregister_mcp(project=False, cwd=tmp_path)
    assert "mnemo" not in json.loads((tmp_home / ".claude.json").read_text()).get("mcpServers", {})


def test_claude_export_target_and_describe(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host

    host = get_host("claude")
    t = host.export_target(tmp_path)
    assert t.path == tmp_path / ".claude" / "rules" / "mnemo.md"
    s = host.describe(project=False, cwd=tmp_path)
    assert s.name == "claude" and s.registered is False
    host.register_mcp(project=False, cwd=tmp_path)
    s = host.describe(project=False, cwd=tmp_path)
    assert s.registered is True and Path(s.path) == tmp_home / ".claude.json"
