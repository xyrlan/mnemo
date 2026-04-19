"""Backwards-compat shim for ``mnemo.cli`` (v0.9 PR H).

The 1294-line ``cli.py`` was split into a package in v0.9 PR H. This
shim re-exports the names the public API surface test pins
(``main``, ``COMMANDS``, ``_resolve_vault``), the symbols every
``cmd_*`` looks up at call-time so monkeypatch.setattr against
``mnemo.cli.<name>`` propagates (``_resolve_vault``, ``_run_open``),
and the three ``_doctor_check_reflex_*`` helpers
``test_cli_status_doctor_reflex`` reads as attributes of
``mnemo.cli``. Importing the ``commands`` submodule also triggers
each ``@command`` decorator so the ``COMMANDS`` registry is
populated by package-load time.

New code should import from concrete submodules
(``mnemo.cli.runtime``, ``mnemo.cli.commands.<name>``,
``mnemo.cli.commands.doctor_checks.<concern>``).
"""
from __future__ import annotations

from mnemo.cli import commands  # noqa: F401  — trigger @command registration
from mnemo.cli.commands.doctor_checks.reflex import (  # noqa: F401
    _doctor_check_reflex_bilingual_gap,
    _doctor_check_reflex_index,
    _doctor_check_reflex_session_cap_hits,
)
from mnemo.cli.parser import COMMANDS  # noqa: F401
from mnemo.cli.runtime import (  # noqa: F401
    _resolve_vault,
    _run_open,
    main,
)
