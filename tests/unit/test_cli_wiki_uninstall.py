from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo import cli


def _init(tmp_home: Path) -> Path:
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    return tmp_home / "v"


def test_uninstall_removes_hooks_keeps_vault(tmp_home: Path):
    vault = _init(tmp_home)
    settings_path = tmp_home / ".claude" / "settings.json"
    rc = cli.main(["uninstall", "--yes"])
    assert rc == 0
    assert vault.exists()  # vault preserved
    data = json.loads(settings_path.read_text())
    cmds = [
        h.get("command", "")
        for ev in data.get("hooks", {}).values()
        for entry in ev
        for h in entry.get("hooks", [])
    ]
    assert not any("mnemo" in c for c in cmds)


def test_uninstall_interactive_aborts_on_no(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    _init(tmp_home)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")
    rc = cli.main(["uninstall"])
    assert rc != 0
