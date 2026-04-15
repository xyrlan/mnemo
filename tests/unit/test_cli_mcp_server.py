"""Tests for the hidden ``mnemo mcp-server`` CLI subcommand."""
from __future__ import annotations

import pytest

from mnemo.cli import COMMANDS, main


def test_mcp_server_subcommand_is_registered():
    assert "mcp-server" in COMMANDS


def test_mcp_server_subcommand_invokes_serve(monkeypatch):
    called: dict[str, bool] = {}

    def fake_serve(stdin=None, stdout=None):
        called["yes"] = True
        return 0

    monkeypatch.setattr("mnemo.core.mcp.server.serve", fake_serve)
    rc = main(["mcp-server"])
    assert rc == 0
    assert called.get("yes") is True


def test_mcp_server_subcommand_propagates_nonzero_exit(monkeypatch):
    monkeypatch.setattr("mnemo.core.mcp.server.serve", lambda **kw: 7)
    assert main(["mcp-server"]) == 7
