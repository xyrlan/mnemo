"""#303: a hook installed before its matcher widened must show up in doctor.

``inject_hooks`` writes the matcher once; #271 added ``Read`` to ``PreToolUse``
and every existing install kept the old one while ``status`` said healthy.
The fixture below is the shape on the maintainer's machine, including the
unrelated empty-matcher entry another tool adds to the same event.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.cli.commands import doctor
from mnemo.cli.commands.doctor_checks import hook_matcher as hm
from mnemo.install.settings import HOOK_DEFINITIONS, inject_hooks

MNEMO = "/usr/local/bin/python3 -m mnemo.hooks.pre_tool_use"
OTHER = '"/Users/x/Library/Application Support/GitKrakenCLI/gk" ai hook run'


def _pre_tool_use(*entries: tuple[object, str]) -> dict:
    out = []
    for matcher, command in entries:
        entry: dict = {"hooks": [{"type": "command", "command": command}]}
        if matcher is not ...:
            entry["matcher"] = matcher
        out.append(entry)
    return {"hooks": {"PreToolUse": out}}


def _write(dir_: Path, data: dict) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    path = dir_ / "settings.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_pre_271_matcher_is_missing_read():
    data = _pre_tool_use(("Bash|Edit|Write|MultiEdit", MNEMO), ("", OTHER))
    assert hm.missing_tools(data) == {"PreToolUse": ["Read"]}


def test_current_matcher_is_silent():
    shipped = HOOK_DEFINITIONS["PreToolUse"]["matcher"]
    assert hm.missing_tools(_pre_tool_use((shipped, MNEMO))) == {}


def test_other_tools_empty_matcher_does_not_cover_mnemo():
    # The "" entry belongs to another tool; it must not count as mnemo's.
    data = _pre_tool_use(("Bash|Edit", MNEMO), ("", OTHER))
    assert hm.missing_tools(data)["PreToolUse"] == ["Read", "Write", "MultiEdit"]


@pytest.mark.parametrize("matcher", ["", "*", ...])
def test_match_all_matcher_is_silent(matcher):
    assert hm.missing_tools(_pre_tool_use((matcher, MNEMO))) == {}


def test_regex_matcher_is_not_judged():
    assert hm.missing_tools(_pre_tool_use(("Edit.*|Bash", MNEMO))) == {}


def test_wider_user_matcher_is_silent():
    shipped = HOOK_DEFINITIONS["PreToolUse"]["matcher"]
    assert hm.missing_tools(_pre_tool_use((shipped + "|NotebookEdit", MNEMO))) == {}


def test_duplicate_entries_cover_each_other():
    data = _pre_tool_use(("Bash|Edit|Write|MultiEdit", MNEMO), ("Read", MNEMO))
    assert hm.missing_tools(data) == {}


def test_binary_install_command_is_judged():
    data = _pre_tool_use(("Bash|Edit|Write|MultiEdit", "/opt/bin/mnemo hook pre_tool_use"))
    assert hm.missing_tools(data) == {"PreToolUse": ["Read"]}


def test_no_mnemo_hook_is_silent():
    assert hm.missing_tools(_pre_tool_use(("Bash", OTHER))) == {}
    assert hm.missing_tools({}) == {}
    assert hm.missing_tools({"hooks": []}) == {}


def test_doctor_row_warns_and_names_the_non_destructive_remedy(tmp_path, capsys):
    claude = tmp_path / "home" / ".claude"
    _write(claude, _pre_tool_use(("Bash|Edit|Write|MultiEdit", MNEMO), ("", OTHER)))
    cwd = tmp_path / "repo"
    cwd.mkdir()

    assert hm._doctor_check_hook_matcher(tmp_path, claude_dir=claude, cwd=cwd) is False
    out = capsys.readouterr().out
    assert "PreToolUse" in out and "Read never reaches it" in out
    assert "`mnemo init --hooks-only`" in out
    assert "--project" not in out


def test_doctor_row_reports_project_scope_with_project_remedy(tmp_path, capsys):
    claude = tmp_path / "home" / ".claude"
    cwd = tmp_path / "repo"
    _write(cwd / ".claude", _pre_tool_use(("Bash|Edit|Write|MultiEdit", MNEMO)))

    assert hm._doctor_check_hook_matcher(tmp_path, claude_dir=claude, cwd=cwd) is False
    assert "`mnemo init --project --hooks-only`" in capsys.readouterr().out


def test_doctor_row_silent_after_inject_hooks(tmp_path, capsys):
    # The remedy it names must be what makes it go quiet.
    claude = tmp_path / "home" / ".claude"
    path = _write(claude, _pre_tool_use(("Bash|Edit|Write|MultiEdit", MNEMO), ("", OTHER)))
    inject_hooks(path)

    assert hm._doctor_check_hook_matcher(tmp_path, claude_dir=claude, cwd=tmp_path) is True
    assert capsys.readouterr().out == ""
    # The other tool's entry survived the rewrite.
    entries = json.loads(path.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert any(h["command"] == OTHER for e in entries for h in e["hooks"])


def test_doctor_row_silent_on_malformed_settings(tmp_path, capsys):
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text("{not json", encoding="utf-8")
    assert hm._doctor_check_hook_matcher(tmp_path, claude_dir=claude, cwd=tmp_path) is True


def test_registered_in_doctor():
    assert dict(doctor.DOCTOR_CHECKS)["hook_matcher"] is hm._doctor_check_hook_matcher
