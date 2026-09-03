from __future__ import annotations

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
