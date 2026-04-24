"""Command sub-package — one module per CLI command.

Each module decorates its handler with ``@command("<name>")`` from
:mod:`mnemo.cli.parser`. The decorator runs at import time, so we
must import every command module here for the registry to be
populated by the time :func:`mnemo.cli.runtime.main` looks up a
handler.
"""
from __future__ import annotations

from mnemo.cli.commands import (  # noqa: F401  — trigger @command registration
    briefing,
    dedup_rules,
    disable_rule,
    doctor,
    extract,
    init,
    list_enforced,
    migrate_worktree_briefings,
    misc,
    recall,
    statusline,
    status,
    telemetry,
)
