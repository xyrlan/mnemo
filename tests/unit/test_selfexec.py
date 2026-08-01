"""How mnemo invokes itself, under both a package install and a frozen build.

Every self-spawn in the codebase assumes ``sys.executable`` is a Python
interpreter and prepends ``-m mnemo``. In a frozen build ``sys.executable`` is
the mnemo binary itself, so that prefix turns into garbage arguments: the
detached extract, the briefing writer, all five autopilot jobs, and the hook
and statusLine commands written into settings.json would each fail in a
different way. These tests pin both shapes.
"""
from __future__ import annotations

import sys

import pytest

from mnemo import _selfexec


@pytest.fixture
def as_package(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")


@pytest.fixture
def as_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/usr/local/bin/mnemo")


def test_is_frozen_reflects_the_interpreter_shape(as_package):
    assert _selfexec.is_frozen() is False


def test_is_frozen_true_under_a_frozen_build(as_frozen):
    assert _selfexec.is_frozen() is True


def test_self_argv_goes_through_dash_m_for_a_package_install(as_package):
    assert _selfexec.self_argv("extract", "--background") == [
        "/usr/bin/python3", "-m", "mnemo", "extract", "--background",
    ]


def test_self_argv_calls_the_binary_directly_when_frozen(as_frozen):
    assert _selfexec.self_argv("extract", "--background") == [
        "/usr/local/bin/mnemo", "extract", "--background",
    ]


def test_self_command_keeps_the_dash_m_form_for_a_package_install(as_package):
    assert _selfexec.self_command("statusline-compose") == (
        "/usr/bin/python3 -m mnemo statusline-compose"
    )


def test_self_command_drops_dash_m_when_frozen(as_frozen):
    assert _selfexec.self_command("statusline-compose") == (
        "/usr/local/bin/mnemo statusline-compose"
    )


def test_hook_command_uses_the_module_path_for_a_package_install(as_package):
    """Unchanged from what is already on every existing user's disk."""
    assert _selfexec.hook_command("session_start") == (
        "/usr/bin/python3 -m mnemo.hooks.session_start"
    )


def test_hook_command_uses_the_subcommand_when_frozen(as_frozen):
    assert _selfexec.hook_command("session_start") == (
        "/usr/local/bin/mnemo hook session_start"
    )


def test_commands_are_posix_ified_on_windows(monkeypatch):
    """Claude Code dispatches hook commands through bash, which eats ``\\``."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Python311\python.exe")

    assert _selfexec.hook_command("session_end") == (
        "C:/Python311/python.exe -m mnemo.hooks.session_end"
    )
    assert _selfexec.self_command("statusline-compose") == (
        "C:/Python311/python.exe -m mnemo statusline-compose"
    )


def test_argv_is_not_posix_ified(monkeypatch):
    """argv goes to subprocess directly, not through a shell — leave it alone."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Python311\python.exe")

    assert _selfexec.self_argv("extract")[0] == r"C:\Python311\python.exe"


def test_falls_back_to_python3_when_executable_is_unknown(monkeypatch):
    """Embedded interpreters can leave sys.executable empty."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", "")

    assert _selfexec.hook_command("session_start") == "python3 -m mnemo.hooks.session_start"
    assert _selfexec.self_argv("extract")[0] == "python3"


def test_python_argv_still_targets_a_real_interpreter_when_frozen(as_frozen, monkeypatch):
    """Spawning *Python* (not mnemo) must not resolve to the frozen binary.

    The autopilot self-fix gates run pytest; under a frozen build
    ``sys.executable`` is mnemo, so ``[sys.executable, "-m", "pytest"]`` would
    be parsed as mnemo CLI arguments instead of running the test suite.
    """
    monkeypatch.setattr(_selfexec.shutil, "which", lambda name: f"/usr/bin/{name}")

    assert _selfexec.python_argv("-m", "pytest") == ["/usr/bin/python3", "-m", "pytest"]


def test_python_argv_is_none_when_no_interpreter_is_available(as_frozen, monkeypatch):
    monkeypatch.setattr(_selfexec.shutil, "which", lambda name: None)

    assert _selfexec.python_argv("-m", "pytest") is None


def test_python_argv_uses_the_running_interpreter_for_a_package_install(as_package):
    assert _selfexec.python_argv("-m", "pytest") == ["/usr/bin/python3", "-m", "pytest"]
