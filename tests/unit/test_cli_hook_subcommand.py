"""``mnemo hook <event>`` — the binary-invocable equivalent of ``-m mnemo.hooks.X``.

A frozen build has no importable module path to hand to ``python -m``, so the
four hook modules need a CLI entry point. This is the command that gets written
into settings.json by a standalone install, which makes it as load-bearing as
the hooks themselves: if it stops dispatching, every session silently loses
capture, injection, and enforcement.
"""
from __future__ import annotations

import pytest

from mnemo.cli.parser import COMMANDS, INTERNAL_COMMANDS
from mnemo.install.settings import HOOK_MODULES


def _run(monkeypatch, event: str, calls: list[str]) -> int:
    import argparse

    for module in HOOK_MODULES:
        mod = __import__(f"mnemo.hooks.{module}", fromlist=["main"])
        monkeypatch.setattr(mod, "main", lambda _m=module: (calls.append(_m), 0)[1])

    return COMMANDS["hook"](argparse.Namespace(event=event))


def test_hook_is_registered_as_an_internal_command():
    assert "hook" in COMMANDS
    assert "hook" in INTERNAL_COMMANDS, "must not appear in `mnemo help`"


@pytest.mark.parametrize("event", sorted(HOOK_MODULES))
def test_each_event_dispatches_to_its_module_main(monkeypatch, event: str):
    calls: list[str] = []

    assert _run(monkeypatch, event, calls) == 0

    assert calls == [event]


def test_every_installed_hook_event_is_dispatchable():
    """The parser's choices and the events we install must not drift apart."""
    from mnemo.cli.parser import _build_parser

    parser = _build_parser()
    for event in HOOK_MODULES:
        args = parser.parse_args(["hook", event])
        assert args.event == event


def test_unknown_event_is_rejected_by_the_parser():
    from mnemo.cli.parser import _build_parser

    with pytest.raises(SystemExit):
        _build_parser().parse_args(["hook", "not_a_real_event"])


def test_dispatch_is_fail_open_when_the_hook_raises(monkeypatch, capsys):
    """A crashing hook must not surface a traceback into the Claude Code session.

    The hook modules already guard themselves, but the dispatcher is a new layer
    between Claude Code and them, so it needs the same contract.
    """
    import argparse

    mod = __import__("mnemo.hooks.session_start", fromlist=["main"])

    def boom():
        raise RuntimeError("hook exploded")

    monkeypatch.setattr(mod, "main", boom)

    assert COMMANDS["hook"](argparse.Namespace(event="session_start")) == 0
