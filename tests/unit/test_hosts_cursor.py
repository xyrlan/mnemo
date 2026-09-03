from __future__ import annotations

import json
from pathlib import Path


def test_cursor_registers_global_and_project(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host

    host = get_host("cursor")
    r = host.register_mcp(project=False, cwd=tmp_path)
    assert Path(r.path) == tmp_home / ".cursor" / "mcp.json" and r.method == "json"
    data = json.loads((tmp_home / ".cursor" / "mcp.json").read_text())
    assert data["mcpServers"]["mnemo"]["args"][-1] == "mcp-server"

    r2 = host.register_mcp(project=True, cwd=tmp_path)
    assert Path(r2.path) == tmp_path / ".cursor" / "mcp.json"


def test_cursor_preserves_other_servers_and_unregisters(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host

    p = tmp_home / ".cursor" / "mcp.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    host = get_host("cursor")
    host.register_mcp(project=False, cwd=tmp_path)
    data = json.loads(p.read_text())
    assert set(data["mcpServers"]) == {"other", "mnemo"}
    host.unregister_mcp(project=False, cwd=tmp_path)
    assert set(json.loads(p.read_text())["mcpServers"]) == {"other"}


def test_cursor_export_target_and_describe(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host

    host = get_host("cursor")
    assert host.export_target(tmp_path).path == tmp_path / ".cursor" / "rules" / "mnemo.mdc"
    assert host.describe(project=False, cwd=tmp_path).registered is False
    host.register_mcp(project=False, cwd=tmp_path)
    s = host.describe(project=False, cwd=tmp_path)
    assert s.registered is True and s.command_ok is True   # command is this python
