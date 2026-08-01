"""Ownership detection for mnemo-installed hook commands.

``mnemo.hooks.`` used to do double duty: it was both the module path that made
the hook executable *and* the substring that identified mnemo-owned entries in
settings.json. A standalone build invokes ``<path>/mnemo hook session_start``
instead, which is still ours but contains no such substring — so uninstall,
init idempotency, and hook-health counting would all stop recognising it.

These tests pin the predicate that both forms must satisfy, and the negatives
that must keep being someone else's business.
"""
from __future__ import annotations

import pytest

from mnemo.install.settings import is_mnemo_hook_command


@pytest.mark.parametrize("command", [
    "/usr/bin/python3 -m mnemo.hooks.session_start",
    "/opt/homebrew/bin/python3.11 -m mnemo.hooks.user_prompt_submit",
    "C:/Users/x/AppData/Local/Programs/Python/Python311/python.exe -m mnemo.hooks.pre_tool_use",
    "python3 -m mnemo.hooks.session_end",
])
def test_recognises_the_legacy_module_form(command: str):
    assert is_mnemo_hook_command(command) is True


@pytest.mark.parametrize("command", [
    "/usr/local/bin/mnemo hook session_start",
    "/home/x/.local/share/claude/plugins/mnemo/bin/mnemo hook user_prompt_submit",
    "C:/Users/x/AppData/Roaming/npm/mnemo.exe hook pre_tool_use",
    "mnemo hook session_end",
])
def test_recognises_the_standalone_binary_form(command: str):
    assert is_mnemo_hook_command(command) is True


def test_recognises_a_quoted_binary_path_with_spaces():
    assert is_mnemo_hook_command('"/Applications/My Tools/mnemo" hook session_start') is True


@pytest.mark.parametrize("command", [
    "",
    "python3 -m other.hooks.session_start",
    "/usr/bin/some-other-tool hook session_start",
    # A user's own script that merely lives under a path containing "mnemo".
    # status.py used to match a bare `"mnemo" in command`, which claimed this.
    "/home/x/mnemo-notes/bin/backup.sh",
    "python3 /home/x/projects/mnemo/scripts/custom.py",
    # Our binary, but not a hook invocation — `mnemo status` in a statusLine
    # or a slash command must not be mistaken for an installed hook.
    "/usr/local/bin/mnemo status",
    "/usr/local/bin/mnemo statusline-compose",
])
def test_does_not_claim_commands_that_are_not_mnemo_hooks(command: str):
    assert is_mnemo_hook_command(command) is False


def test_does_not_claim_an_unknown_hook_event():
    """Only the four events mnemo actually installs count as ours."""
    assert is_mnemo_hook_command("/usr/local/bin/mnemo hook not_a_real_event") is False


def test_tolerates_a_non_string_command():
    assert is_mnemo_hook_command(None) is False  # type: ignore[arg-type]
