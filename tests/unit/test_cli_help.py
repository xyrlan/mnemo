"""`mnemo --help` and `mnemo help` say the same thing (#437).

Before this, `-h`/`--help` was argparse's own default action: it dumped
every subparser, unfiltered by ADVANCED_COMMANDS/INTERNAL_COMMANDS, so the
"curated" help was only ever the six letters away — `mnemo help` — that
nobody typed instead of the conventional flag. This pins that both spellings
now render the same curated list, and that the one-sentence description
shown there agrees with the README's opening, `pyproject.toml`, and the
plugin manifest — the drift the issue is about.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import mnemo.cli.commands  # noqa: F401 — populates COMMANDS
from mnemo.cli.parser import (
    ADVANCED_COMMANDS,
    INTERNAL_COMMANDS,
    TAGLINE,
    _build_parser,
)

REPO = Path(__file__).resolve().parents[2]


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _help_text(capsys, argv: list[str]) -> str:
    parser = _build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(argv)
    assert exc.value.code == 0
    return _ANSI.sub("", capsys.readouterr().out)


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_top_level_help_hides_advanced_and_internal_commands(capsys, flag):
    out = _help_text(capsys, [flag])
    for hidden in ADVANCED_COMMANDS | INTERNAL_COMMANDS:
        # A prose word like "hook health" in another command's help text is
        # fine; only a listed command entry (start-of-line, own column) is not.
        assert not re.search(rf"^\s+{re.escape(hidden)}\s", out, re.MULTILINE), (
            f"{hidden!r} should be hidden from --help"
        )
    # A user-facing verb from the measured top of the table (#437) is there.
    assert re.search(r"^\s+dispatch\s", out, re.MULTILINE)
    assert re.search(r"^\s+sessions\s", out, re.MULTILINE)


def test_help_flag_and_help_command_render_the_same_list(capsys):
    flag_out = _help_text(capsys, ["--help"])

    from mnemo.cli.commands.misc import cmd_help
    import argparse
    cmd_help(argparse.Namespace(all=False))
    command_out = _ANSI.sub("", capsys.readouterr().out)

    assert flag_out == command_out


def test_help_flag_names_how_many_advanced_commands_are_hidden(capsys):
    out = _help_text(capsys, ["--help"])
    assert f"({len(ADVANCED_COMMANDS)} advanced commands hidden" in out


def test_help_all_still_reaches_advanced_commands():
    parser = _build_parser()
    ns = parser.parse_args(["help", "--all"])
    assert ns.command == "help" and ns.all is True


def test_top_level_description_is_the_tagline():
    assert _build_parser().description == TAGLINE


def test_tagline_agrees_with_pyproject_and_plugin_manifest():
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^description = "(.*)"$', pyproject, re.MULTILINE)
    assert match, "pyproject.toml has no [project].description"
    assert match.group(1) == TAGLINE

    plugin = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert plugin["description"] == TAGLINE

    marketplace = json.loads(
        (REPO / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    entry = next(p for p in marketplace["plugins"] if p["name"] == "mnemo")
    assert entry["description"] == TAGLINE


def test_tagline_agrees_with_the_readme_opening():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    lines = readme.splitlines()
    quote = " ".join(
        line[2:].strip() for line in lines if line.startswith(">")
    )
    assert quote == TAGLINE


# Every argv the issue names as a caller mnemo cannot break: hooks, the
# skills, the plugin's slash commands, and mnemo-desktop's allowlist
# (src-tauri/src/vault.rs:1147). Curating --help changes nothing about how
# these parse — this is the regression pin that says so.
_CALLERS_ARGV = [
    ["hook", "session_start"],
    ["sessions", "--consume-unblocks"],
    ["child-report", "aaaa0001", "--parent", "p"],
    ["statusline"],
    ["statusline", "--install"],
    ["statusline-compose"],
    ["disable-rule", "some-slug"],
    ["why"],
    ["reverify"],
    ["rewrites"],
    ["learn"],
    ["status"],
    ["stale"],
]


@pytest.mark.parametrize("argv", _CALLERS_ARGV, ids=[" ".join(a) for a in _CALLERS_ARGV])
def test_every_documented_caller_still_parses_unchanged(argv):
    ns = _build_parser().parse_args(argv)
    assert ns.command == argv[0]
