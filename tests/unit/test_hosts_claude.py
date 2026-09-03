from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest


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


def test_json_registration_missing_file(tmp_path: Path):
    from mnemo.hosts.claude import json_registration

    assert json_registration(tmp_path / "does-not-exist.json") == (False, "")


def test_json_registration_invalid_json(tmp_path: Path):
    from mnemo.hosts.claude import json_registration

    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert json_registration(p) == (False, "")


def test_json_registration_mcpservers_not_a_dict(tmp_path: Path):
    from mnemo.hosts.claude import json_registration

    p = tmp_path / "list.json"
    p.write_text(json.dumps({"mcpServers": [1]}), encoding="utf-8")
    assert json_registration(p) == (False, "")


def test_json_registration_command_not_a_string(tmp_path: Path):
    from mnemo.hosts.claude import json_registration

    p = tmp_path / "badcmd.json"
    p.write_text(json.dumps({"mcpServers": {"mnemo": {"command": 7}}}), encoding="utf-8")
    assert json_registration(p) == (True, "")


def test_json_registration_good_entry(tmp_path: Path):
    from mnemo.hosts.claude import json_registration

    p = tmp_path / "good.json"
    p.write_text(json.dumps({"mcpServers": {"mnemo": {"command": "mnemo"}}}), encoding="utf-8")
    assert json_registration(p) == (True, "mnemo")


def test_command_exists_executable_file():
    from mnemo.hosts.claude import command_exists

    assert command_exists(sys.executable) is True


def test_command_exists_directory_is_false(tmp_path: Path):
    from mnemo.hosts.claude import command_exists

    assert command_exists(str(tmp_path)) is False


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no executable bit; os.access(X_OK) is always true")
def test_command_exists_non_executable_file_is_false(tmp_path: Path):
    from mnemo.hosts.claude import command_exists

    p = tmp_path / "not_executable.txt"
    p.write_text("hi", encoding="utf-8")
    p.chmod(0o644)
    assert command_exists(str(p)) is False


def test_command_exists_nonexistent_path_is_false():
    from mnemo.hosts.claude import command_exists

    assert command_exists("/nonexistent/x") is False


def test_command_exists_bare_name_on_path():
    from mnemo.hosts.claude import command_exists

    if shutil.which("ls") is None:
        pytest.skip("ls not on PATH")
    assert command_exists("ls") is True


def test_command_exists_empty_is_false():
    from mnemo.hosts.claude import command_exists

    assert command_exists("") is False


def test_describe_registered_with_no_command_detail(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host

    path = tmp_home / ".claude.json"
    path.write_text(json.dumps({"mcpServers": {"mnemo": {}}}), encoding="utf-8")
    host = get_host("claude")
    s = host.describe(project=False, cwd=tmp_path)
    assert s.registered is True
    assert s.command_ok is False
    assert s.detail == "registration has no command"
