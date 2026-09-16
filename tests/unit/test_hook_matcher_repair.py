"""#337: a matcher this version ships has to reach installs that already exist.

#303 gave hook-matcher drift a ``doctor`` row and ``init --hooks-only`` to
repair it. Both are opt-in, so the maintainer's own install ran a week at half
reach (23 enrichment notes where the current matcher delivers 40) while
``mnemo status`` called the hook healthy. These tests pin the two halves that
close that: session start repairs the drift it finds, and ``status`` — the
command people actually run — says so.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import pytest

from mnemo.cli.parser import COMMANDS
from mnemo.hooks import session_start
from mnemo.install import hook_drift
from mnemo.install.settings import HOOK_DEFINITIONS

MNEMO = "/usr/local/bin/python3 -m mnemo.hooks.pre_tool_use"
OTHER = '"/Users/x/Library/Application Support/GitKrakenCLI/gk" ai hook run'
PRE_271 = "Bash|Edit|Write|MultiEdit"


def _settings(dir_: Path, matcher: object = PRE_271, command: str = MNEMO) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    entry: dict = {"hooks": [{"type": "command", "command": command}]}
    if matcher is not ...:
        entry["matcher"] = matcher
    path = dir_ / "settings.json"
    path.write_text(
        json.dumps({
            "statusLine": {"command": "mine"},
            "hooks": {"PreToolUse": [
                entry,
                {"matcher": "", "hooks": [{"type": "command", "command": OTHER}]},
            ]},
        }),
        encoding="utf-8",
    )
    return path


def _matchers(path: Path) -> list[object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [e.get("matcher") for e in data["hooks"]["PreToolUse"]]


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    (v / ".mnemo").mkdir(parents=True)
    return v


# --- the repair itself -----------------------------------------------------


def test_auto_repair_rewrites_the_stale_matcher(vault: Path, tmp_path: Path):
    claude = tmp_path / "home" / ".claude"
    path = _settings(claude)

    notices = hook_drift.auto_repair(vault, claude_dir=claude, cwd=tmp_path / "nowhere")

    assert HOOK_DEFINITIONS["PreToolUse"]["matcher"] in _matchers(path)
    assert hook_drift.scan(claude, tmp_path / "nowhere") == []
    assert len(notices) == 1
    assert "Read" in notices[0] and "autoRepairHooks" in notices[0]


def test_repair_leaves_everything_that_is_not_a_mnemo_hook(vault: Path, tmp_path: Path):
    claude = tmp_path / "home" / ".claude"
    path = _settings(claude)

    hook_drift.auto_repair(vault, claude_dir=claude, cwd=tmp_path / "nowhere")

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["statusLine"] == {"command": "mine"}, "#303: init --hooks-only touches hooks only"
    commands = [h["command"] for e in data["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert OTHER in commands


def test_a_current_install_is_never_written_to(vault: Path, tmp_path: Path):
    claude = tmp_path / "home" / ".claude"
    path = _settings(claude, matcher=HOOK_DEFINITIONS["PreToolUse"]["matcher"])
    before = path.read_text(encoding="utf-8")

    assert hook_drift.auto_repair(vault, claude_dir=claude, cwd=tmp_path / "nowhere") == []
    assert path.read_text(encoding="utf-8") == before
    assert not (vault / hook_drift.REPAIR_MARKER_REL).exists()


def test_a_plugin_install_is_not_repaired(vault: Path, tmp_path: Path):
    """A plugin declares its hooks in its own hooks.json; settings.json is not
    mnemo's to write, and the plugin copy cannot drift — it ships with the
    code and a manifest test holds it to ``HOOK_DEFINITIONS``."""
    claude = tmp_path / "home" / ".claude"
    path = _settings(claude, matcher="Bash", command=OTHER)
    before = path.read_text(encoding="utf-8")

    assert hook_drift.auto_repair(vault, claude_dir=claude, cwd=tmp_path / "nowhere") == []
    assert path.read_text(encoding="utf-8") == before


# --- the marker: repair once, then leave the user alone --------------------


def test_a_matcher_narrowed_back_by_hand_is_not_fought(vault: Path, tmp_path: Path):
    claude = tmp_path / "home" / ".claude"
    path = _settings(claude)
    hook_drift.auto_repair(vault, claude_dir=claude, cwd=tmp_path / "nowhere")

    _settings(claude)  # the user re-narrows it on purpose
    assert hook_drift.auto_repair(vault, claude_dir=claude, cwd=tmp_path / "nowhere") == []
    assert PRE_271 in _matchers(path)
    # doctor still reports it — the signal survives, only the write stops.
    assert hook_drift.scan(claude, tmp_path / "nowhere")[0].missing == {"PreToolUse": ["Read"]}


def test_the_next_release_that_widens_a_matcher_is_repaired_again(
    vault: Path, tmp_path: Path, monkeypatch
):
    """The class #337 names: this must hold for the matcher change after Read."""
    claude = tmp_path / "home" / ".claude"
    path = _settings(claude)
    assert hook_drift.auto_repair(vault, claude_dir=claude, cwd=tmp_path / "nowhere")

    monkeypatch.setitem(
        HOOK_DEFINITIONS["PreToolUse"], "matcher",
        HOOK_DEFINITIONS["PreToolUse"]["matcher"] + "|NotebookEdit",
    )
    notices = hook_drift.auto_repair(vault, claude_dir=claude, cwd=tmp_path / "nowhere")

    assert len(notices) == 1 and "NotebookEdit" in notices[0]
    assert "NotebookEdit" in " ".join(str(m) for m in _matchers(path))


def test_a_failed_repair_is_retried_next_session(vault: Path, tmp_path: Path, monkeypatch):
    """Another session holding the settings.json lock must not burn the drift."""
    from mnemo.install.settings import SettingsError

    claude = tmp_path / "home" / ".claude"
    path = _settings(claude)
    monkeypatch.setattr(
        hook_drift, "repair",
        lambda drift: (_ for _ in ()).throw(SettingsError("lock held")),
    )

    assert hook_drift.auto_repair(vault, claude_dir=claude, cwd=tmp_path / "nowhere") == []
    assert not (vault / hook_drift.REPAIR_MARKER_REL).exists()

    monkeypatch.undo()
    assert hook_drift.auto_repair(vault, claude_dir=claude, cwd=tmp_path / "nowhere")
    assert HOOK_DEFINITIONS["PreToolUse"]["matcher"] in _matchers(path)


def test_a_corrupt_marker_does_not_block_the_repair(vault: Path, tmp_path: Path):
    (vault / hook_drift.REPAIR_MARKER_REL).write_text("{ not json", encoding="utf-8")
    claude = tmp_path / "home" / ".claude"
    path = _settings(claude)

    assert hook_drift.auto_repair(vault, claude_dir=claude, cwd=tmp_path / "nowhere")
    assert HOOK_DEFINITIONS["PreToolUse"]["matcher"] in _matchers(path)


# --- the session-start seam ------------------------------------------------


def test_session_start_repairs_and_says_so_on_stderr(vault: Path, tmp_path: Path, capsys):
    """stdout carries the injection envelope, so the notice goes to stderr."""
    claude = tmp_path / "home" / ".claude"
    path = _settings(claude)

    session_start._maybe_repair_hook_matchers(vault, {}, str(tmp_path / "nowhere"))

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "PreToolUse" in captured.err and "Read" in captured.err
    assert HOOK_DEFINITIONS["PreToolUse"]["matcher"] in _matchers(path)


def test_session_start_respects_the_opt_out(vault: Path, tmp_path: Path, capsys):
    claude = tmp_path / "home" / ".claude"
    path = _settings(claude)

    session_start._maybe_repair_hook_matchers(
        vault, {"install": {"autoRepairHooks": False}}, str(tmp_path / "nowhere"),
    )

    assert PRE_271 in _matchers(path)
    assert capsys.readouterr().err == ""


def test_auto_repair_is_on_by_default():
    from mnemo.core.config import DEFAULTS

    assert DEFAULTS["install"]["autoRepairHooks"] is True


# --- the status line -------------------------------------------------------


def _status(monkeypatch, vault: Path, scope: str = "all") -> str:
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault, raising=False)
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(vault)})
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    COMMANDS["status"](argparse.Namespace(scope=scope))
    return buf.getvalue()


def test_status_reports_drift_with_the_one_line_fix(
    vault: Path, tmp_home: Path, tmp_path: Path, monkeypatch
):
    _settings(tmp_home / ".claude")

    out = _status(monkeypatch, vault)

    line = next(ln for ln in out.splitlines() if "predates this version" in ln)
    assert line.startswith("Hooks (global):")
    assert "Read never reaches it" in line
    assert "`mnemo init --hooks-only`" in line


def test_status_is_silent_when_the_matcher_is_current(
    vault: Path, tmp_home: Path, monkeypatch
):
    _settings(tmp_home / ".claude", matcher=HOOK_DEFINITIONS["PreToolUse"]["matcher"])

    assert "predates this version" not in _status(monkeypatch, vault)


def test_status_names_the_project_remedy_for_a_project_install(
    vault: Path, tmp_path: Path, monkeypatch
):
    cwd = tmp_path / "repo"
    _settings(cwd / ".claude")
    monkeypatch.chdir(cwd)

    out = _status(monkeypatch, vault)

    line = next(ln for ln in out.splitlines() if "predates this version" in ln)
    assert line.startswith("Hooks (project):")
    assert "`mnemo init --project --hooks-only`" in line
