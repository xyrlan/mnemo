"""``mnemo statusline --install`` — the opt-in status line.

Claude Code plugins can declare hooks, an MCP server, and commands, but not
the main status line. So under a plugin install the heartbeat
(``mnemo · 9 topics · 7↓ today``) is the one piece that cannot come along
automatically; it becomes a single explicit command instead of a reason to
make everyone open a terminal.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from mnemo.cli.parser import COMMANDS


def _run(**kwargs) -> int:
    return COMMANDS["statusline"](argparse.Namespace(install=False, remove=False, **kwargs))


def test_bare_statusline_still_emits_the_segment(capsys, monkeypatch, tmp_path: Path):
    """Unchanged behaviour: the composer invokes this on every render."""
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(tmp_path)})
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: tmp_path)

    assert _run() == 0


def test_install_wires_the_composer_into_settings(monkeypatch, tmp_path: Path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"model": "opus"}))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(tmp_path)})
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: tmp_path)

    rc = COMMANDS["statusline"](argparse.Namespace(install=True, remove=False))

    assert rc == 0
    data = json.loads(settings.read_text())
    assert data["statusLine"]["command"].endswith("statusline-compose")
    assert data["model"] == "opus", "unrelated settings must survive"


def test_install_is_idempotent(monkeypatch, tmp_path: Path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({}))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(tmp_path)})
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: tmp_path)

    COMMANDS["statusline"](argparse.Namespace(install=True, remove=False))
    first = json.loads(settings.read_text())
    COMMANDS["statusline"](argparse.Namespace(install=True, remove=False))

    assert json.loads(settings.read_text()) == first


def test_install_preserves_an_existing_status_line(monkeypatch, tmp_path: Path):
    """Additive by contract — someone else's status line must not be lost."""
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "statusLine": {"type": "command", "command": "my-own-prompt"}
    }))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(tmp_path)})
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: tmp_path)

    COMMANDS["statusline"](argparse.Namespace(install=True, remove=False))

    saved = json.loads((tmp_path / ".mnemo" / "statusline-original.json").read_text())
    assert saved["command"] == "my-own-prompt"


def test_remove_restores_what_was_there_before(monkeypatch, tmp_path: Path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "statusLine": {"type": "command", "command": "my-own-prompt"}
    }))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(tmp_path)})
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: tmp_path)

    COMMANDS["statusline"](argparse.Namespace(install=True, remove=False))
    COMMANDS["statusline"](argparse.Namespace(install=False, remove=True))

    assert json.loads(settings.read_text())["statusLine"]["command"] == "my-own-prompt"


def test_the_flags_are_reachable_from_the_parser():
    from mnemo.cli.parser import _build_parser

    assert _build_parser().parse_args(["statusline", "--install"]).install is True
    assert _build_parser().parse_args(["statusline", "--remove"]).remove is True
    assert _build_parser().parse_args(["statusline"]).install is False
