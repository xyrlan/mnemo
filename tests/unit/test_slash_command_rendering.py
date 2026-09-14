"""Slash-command bodies must invoke the mnemo that is actually installed.

The slash commands hardcoded ``python3 -m mnemo <cmd>``. That is wrong in
two directions: it ignores a venv install that isn't first on PATH, and under a
frozen build there is no importable ``mnemo`` module for ``-m`` to find, so
every slash command would fail.

The generated plugin manifest has the opposite requirement — it is produced on
a maintainer's machine and committed, so it must stay generic rather than
baking in whatever interpreter did the build.
"""
from __future__ import annotations

import sys

import pytest

from mnemo.install import settings


@pytest.fixture
def as_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/usr/local/bin/mnemo")


@pytest.fixture
def as_package(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/venv/bin/python3")


def test_every_command_declares_its_argv():
    for name, spec in settings.SLASH_COMMANDS.items():
        assert spec["args"], f"{name} has no args"
        assert isinstance(spec["args"], tuple)


def test_rendered_body_uses_the_running_interpreter(as_package):
    body = settings._render_slash_command("status", settings.SLASH_COMMANDS["status"])

    assert "!`/opt/venv/bin/python3 -m mnemo status`" in body


def test_rendered_body_calls_the_binary_directly_when_frozen(as_frozen):
    body = settings._render_slash_command("status", settings.SLASH_COMMANDS["status"])

    assert "!`/usr/local/bin/mnemo status`" in body


def test_multi_word_commands_keep_their_flags(as_frozen):
    body = settings._render_slash_command(
        "uninstall-project", settings.SLASH_COMMANDS["uninstall-project"]
    )

    assert "!`/usr/local/bin/mnemo uninstall --project`" in body


def test_rendered_body_keeps_the_ownership_tag_and_frontmatter(as_package):
    body = settings._render_slash_command("doctor", settings.SLASH_COMMANDS["doctor"])

    assert settings.SLASH_COMMAND_TAG in body
    assert "allowed-tools: Bash" in body
    assert "disable-model-invocation: true" in body


def test_frontmatter_is_the_first_line_on_both_install_paths(as_package):
    """The tag used to come first, and Claude Code then read no frontmatter.

    Every `mnemo init` command showed up in the menu with the literal
    `<!-- mnemo:slash-command -->` as its description and, worse, with
    `disable-model-invocation` unread — nine entries the model was never
    meant to see (#233). The tag now sits right under the closing `---`.
    """
    for name, spec in settings.SLASH_COMMANDS.items():
        body = settings._render_slash_command(name, spec)
        assert body.startswith('---\ndescription: "'), name
        assert f"\n---\n{settings.SLASH_COMMAND_TAG}\n" in body, name
    for name, spec in settings.PLUGIN_COMMANDS.items():
        assert settings.render_plugin_command(spec).startswith('---\ndescription: "'), name


def test_dispatch_is_offered_by_both_install_paths():
    """The loop's entry point (#233). Its output names `mnemo sessions`."""
    for table in (settings.SLASH_COMMANDS, settings.PLUGIN_COMMANDS):
        assert table["dispatch"]["args"] == ("dispatch",)
        assert table["dispatch"]["arguments"] is True
        assert "mnemo sessions" in table["dispatch"]["description"]
        assert "mnemo deliver" in table["dispatch"]["description"]


def test_dispatch_passes_the_users_arguments_through(as_frozen):
    """`/mnemo:dispatch 197 --dry-run` has to reach the CLI as typed."""
    body = settings._render_slash_command("dispatch", settings.SLASH_COMMANDS["dispatch"])
    assert "!`/usr/local/bin/mnemo dispatch $ARGUMENTS`" in body
    assert 'argument-hint: "[issue ...] | --contract <path> [--dry-run]"' in body

    plugin = settings.render_plugin_command(settings.PLUGIN_COMMANDS["dispatch"])
    assert '!`"${CLAUDE_PLUGIN_ROOT}/bin/mnemo.cmd" dispatch $ARGUMENTS`' in plugin


def test_commands_without_arguments_render_none():
    """`$ARGUMENTS` on a command that takes none would be an empty word at
    best and a stray user string handed to `mnemo status` at worst."""
    for name, spec in settings.SLASH_COMMANDS.items():
        if name == "dispatch":
            continue
        assert "$ARGUMENTS" not in settings._render_slash_command(name, spec), name


def test_learn_is_offered_by_both_install_paths():
    """The five-minute loop's verb has to be reachable from a slash command."""
    for table in (settings.SLASH_COMMANDS, settings.PLUGIN_COMMANDS):
        assert table["learn"]["args"] == ("learn",)
        assert "fires on your next prompt" in table["learn"]["description"]


def test_learn_renders_with_the_ownership_tag_and_its_description(as_package):
    body = settings._render_slash_command("learn", settings.SLASH_COMMANDS["learn"])

    assert settings.SLASH_COMMAND_TAG in body
    assert 'description: "learn from this session now' in body
    assert "!`/opt/venv/bin/python3 -m mnemo learn`" in body


def test_learn_renders_for_the_plugin_through_the_launcher():
    body = settings.render_plugin_command(settings.PLUGIN_COMMANDS["learn"])

    assert '!`"${CLAUDE_PLUGIN_ROOT}/bin/mnemo.cmd" learn`' in body


def test_the_plugin_offers_exactly_the_five_verbs_plus_help():
    """A slash menu is a menu: nine entries made the daily loop invisible.

    open/fix/statusline/migrate are once-in-a-lifetime commands and stay
    reachable as CLI subcommands, which `mnemo help` lists. `dispatch` is in
    because it is a loop's entry point, not a one-off (#233); `sessions` and
    `deliver` are not, because the queue is designed never to land in a
    session's context and a slash command would put it exactly there.
    """
    assert set(settings.PLUGIN_COMMANDS) == {
        "status", "why", "doctor", "learn", "dispatch", "help",
    }


def test_the_non_plugin_set_adds_only_what_needs_a_terminal_install():
    """init/uninstall exist only where mnemo wired the hooks itself."""
    assert set(settings.SLASH_COMMANDS) == {
        "status", "why", "doctor", "learn", "dispatch", "help",
        "init", "init-project", "uninstall", "uninstall-project",
    }


def test_why_is_offered_by_both_install_paths():
    for table in (settings.SLASH_COMMANDS, settings.PLUGIN_COMMANDS):
        assert table["why"]["args"] == ("why",)


def test_every_frontmatter_parses_as_strict_yaml(as_package):
    """`learn`'s description holds a `: ` and dispatch's hint opens with `[`.

    Unquoted, both are YAML syntax rather than text, and a strict parser
    drops the whole block — the entry then has no description at all.
    """
    import re

    import yaml

    def block(body: str) -> dict:
        m = re.match(r"^---\n(.*?)\n---\n", body, re.S)
        assert m, body[:60]
        return yaml.safe_load(m.group(1))

    for name, spec in settings.SLASH_COMMANDS.items():
        data = block(settings._render_slash_command(name, spec))
        assert data["description"] == spec["description"], name
        assert data["disable-model-invocation"] is True, name
    for name, spec in settings.PLUGIN_COMMANDS.items():
        data = block(settings.render_plugin_command(spec))
        assert data["description"] == spec["description"], name
    hint = block(settings.render_plugin_command(settings.PLUGIN_COMMANDS["dispatch"]))
    assert hint["argument-hint"] == "[issue ...] | --contract <path> [--dry-run]"
