"""Critical test from spec § 10.3."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo import cli


def test_install_uninstall_round_trip(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    pre = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "other"}]}]}}
    settings_path.write_text(json.dumps(pre, indent=2))

    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    after_install = json.loads(settings_path.read_text())
    starts = after_install["hooks"]["SessionStart"]
    cmds_after_install = [
        h.get("command", "")
        for entry in starts
        for h in entry.get("hooks", [])
    ]
    assert any("other" in c for c in cmds_after_install)
    assert any("mnemo" in c for c in cmds_after_install)

    cli.main(["uninstall", "--yes"])
    after_uninstall = json.loads(settings_path.read_text())
    starts2 = after_uninstall.get("hooks", {}).get("SessionStart", [])
    cmds_after_uninstall = [
        h.get("command", "")
        for entry in starts2
        for h in entry.get("hooks", [])
    ]
    assert any("other" in c for c in cmds_after_uninstall)
    assert not any("mnemo" in c for c in cmds_after_uninstall)
    assert (tmp_home / "v" / "HOME.md").exists()  # vault untouched
