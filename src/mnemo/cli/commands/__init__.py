"""Command sub-package — one module per CLI command.

Each module decorates its handler with ``@command("<name>")`` from
:mod:`mnemo.cli.parser`. The decorator runs at import time, so we
must import every command module here for the registry to be
populated by the time :func:`mnemo.cli.runtime.main` looks up a
handler.
"""
from __future__ import annotations

from mnemo.cli.commands import (  # noqa: F401  — trigger @command registration
    autopilot,
    backfill,
    briefing,
    dedup_rules,
    deliver,
    disable_rule,
    dispatch,
    doctor,
    export,
    extract,
    hook,
    import_rules,
    inbox,
    init,
    land,
    learn,
    list_enforced,
    migrate_plugin,
    migrate_worktree_briefings,
    misc,
    procedures,
    publish,
    recall,
    recall_sessions,
    reclassify,
    regen_graph_edges,
    replay,
    resume,
    reverify,
    rewrites,
    session,
    sessions,
    stale,
    statusline,
    status,
    telemetry,
    why,
)
