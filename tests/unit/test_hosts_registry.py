from __future__ import annotations

from pathlib import Path

import pytest


def test_registry_lists_the_three_hosts_and_resolves_by_name():
    from mnemo.hosts import HOSTS, get_host

    assert list(HOSTS) == ["claude", "cursor", "codex"]
    assert get_host("cursor").name == "cursor"
    with pytest.raises(KeyError):
        get_host("vim")


def test_host_status_and_register_result_shapes():
    from mnemo.hosts import HostStatus, RegisterResult

    r = RegisterResult(path="/x/mcp.json", method="json", note=None)
    assert r.path == "/x/mcp.json" and r.method == "json"
    s = HostStatus(name="cursor", registered=True, path="/x", command_ok=True, detail="")
    assert s.registered and s.command_ok


def test_registered_hosts_empty_when_none_registered(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import registered_hosts

    assert list(registered_hosts(tmp_path)) == []


def test_registered_hosts_lists_a_globally_registered_cursor(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host, registered_hosts

    get_host("cursor").register_mcp(project=False, cwd=tmp_path)
    statuses = list(registered_hosts(tmp_path))
    assert len(statuses) == 1
    assert statuses[0].name == "cursor"
    assert statuses[0].registered


def test_registered_hosts_dedupes_codex_global_only_config(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import registered_hosts

    cfg = tmp_home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        '[mcp_servers.mnemo]\ncommand = "python3"\nargs = ["-m", "mnemo", "mcp-server"]\n',
        encoding="utf-8",
    )
    # codex's describe() ignores `project` and always reports the same global
    # config, so probing both scopes must not yield the host twice.
    statuses = list(registered_hosts(tmp_path))
    assert len(statuses) == 1
    assert statuses[0].name == "codex"
