"""Every subcommand the parser offers must have a registered handler.

``@command`` registers at import time and ``cli/commands/__init__.py`` is
the list of imports that makes it happen. A module missing from that list
still parses (its subparser is in ``parser.py``) and still passes its own
tests (they import the module directly) — and ``mnemo <name>`` prints
``unknown command``. #245 landed ``publish`` and ``import`` exactly that
way: 3517 tests green, neither command reachable.
"""
from __future__ import annotations

import argparse

import mnemo.cli.commands  # noqa: F401  — the registration side effect under test
from mnemo.cli.parser import COMMANDS, _build_parser


def _subcommands() -> list[str]:
    parser = _build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return sorted(action.choices)
    raise AssertionError("no subparsers on the mnemo parser")


def test_every_subcommand_has_a_handler():
    names = _subcommands()
    assert names, "parser offers no subcommands"
    missing = [n for n in names if n not in COMMANDS]
    assert not missing, (
        f"subcommands with no @command handler (module not imported in "
        f"cli/commands/__init__.py?): {missing}"
    )


def test_the_two_that_shipped_unreachable_are_reachable():
    assert "publish" in COMMANDS and "import" in COMMANDS
