"""``mnemo telemetry`` — summarize the MCP access log."""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command


@command("telemetry")
def cmd_telemetry(args: argparse.Namespace) -> int:
    """Summarize `.mnemo/mcp-access-log.jsonl` — calls per tool + zero-hit per project."""
    import json as _json
    from mnemo import cli  # late binding so monkeypatch.setattr("mnemo.cli._resolve_vault", ...) takes effect
    from mnemo.core.mcp import access_log_summary as summary_mod

    vault = cli._resolve_vault()
    entries = summary_mod.read_log(vault)
    summary = summary_mod.summarize(entries)

    if bool(getattr(args, "json", False)):
        print(_json.dumps(summary, indent=2))
    else:
        print(summary_mod.format_human(summary))
    return 0
