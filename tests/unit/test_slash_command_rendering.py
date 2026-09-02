"""Slash-command bodies must invoke the mnemo that is actually installed.

The nine slash commands hardcoded ``python3 -m mnemo <cmd>``. That is wrong in
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

    assert body.startswith(settings.SLASH_COMMAND_TAG)
    assert "allowed-tools: Bash" in body
    assert "disable-model-invocation: true" in body


def test_learn_is_offered_by_both_install_paths():
    """The five-minute loop's verb has to be reachable from a slash command."""
    for table in (settings.SLASH_COMMANDS, settings.PLUGIN_COMMANDS):
        assert table["learn"]["args"] == ("learn",)
        assert "fires on your next prompt" in table["learn"]["description"]


def test_learn_renders_with_the_ownership_tag_and_its_description(as_package):
    body = settings._render_slash_command("learn", settings.SLASH_COMMANDS["learn"])

    assert body.startswith(settings.SLASH_COMMAND_TAG)
    assert "description: learn from this session now" in body
    assert "!`/opt/venv/bin/python3 -m mnemo learn`" in body


def test_learn_renders_for_the_plugin_through_the_launcher():
    body = settings.render_plugin_command(settings.PLUGIN_COMMANDS["learn"])

    assert '!`"${CLAUDE_PLUGIN_ROOT}/bin/mnemo.cmd" learn`' in body
