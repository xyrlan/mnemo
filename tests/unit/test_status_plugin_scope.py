"""``mnemo status`` has to tell the truth under a plugin install.

Hook health is read from settings.json. A plugin declares its hooks in its own
hooks.json instead, so settings.json is legitimately empty — and status used
to report "settings.json missing", i.e. "mnemo is not installed", to a user
whose install is working perfectly. The first thing anyone runs when they
suspect a problem must not invent one.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from mnemo.cli.parser import COMMANDS


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    v = tmp_path / "vault"
    (v / ".mnemo").mkdir(parents=True)
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(v)})
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: v)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    return v


def _status(monkeypatch, plugin_root: Path | None) -> str:
    import io
    import sys

    if plugin_root is None:
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    COMMANDS["status"](argparse.Namespace(scope="all"))
    return buf.getvalue()


def _make_plugin(tmp_path: Path) -> Path:
    root = tmp_path / "plugin"
    (root / "hooks").mkdir(parents=True)
    (root / "hooks" / "hooks.json").write_text(json.dumps({
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "x hook session_start"}]}],
            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "x hook user_prompt_submit"}]}],
            "PreToolUse": [{"matcher": "Bash|Edit|Write|MultiEdit",
                            "hooks": [{"type": "command", "command": "x hook pre_tool_use"}]}],
            "SessionEnd": [{"hooks": [{"type": "command", "command": "x hook session_end"}]}],
        }
    }))
    return root


def test_reports_hooks_as_plugin_managed(vault: Path, tmp_path: Path, monkeypatch):
    plugin = _make_plugin(tmp_path)

    out = _status(monkeypatch, plugin)

    line = next(ln for ln in out.splitlines() if ln.startswith("Hooks"))
    assert "4/4" in line
    assert "plugin" in line.lower()
    assert "missing" not in line.lower()


def test_does_not_claim_a_plugin_install_when_there_is_none(vault: Path, monkeypatch):
    """Outside a plugin, the old settings.json reporting is still correct."""
    out = _status(monkeypatch, None)

    hook_lines = [ln for ln in out.splitlines() if ln.startswith("Hooks")]
    assert hook_lines, "status must still report hook health"
    # Judge the label, not the path after the dash: the tmp home lives under a
    # directory named after this test, which itself contains "plugin".
    labels = [ln.split(" — ")[0] for ln in hook_lines]
    assert all("plugin" not in label.lower() for label in labels)
    assert any("settings.json" in label for label in labels)


def test_a_plugin_with_an_unreadable_hooks_file_is_not_a_crash(
    vault: Path, tmp_path: Path, monkeypatch
):
    plugin = tmp_path / "plugin"
    (plugin / "hooks").mkdir(parents=True)
    (plugin / "hooks" / "hooks.json").write_text("{ not json")

    out = _status(monkeypatch, plugin)

    assert out  # produced something rather than raising
