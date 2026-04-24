"""Argparse wiring + the COMMANDS registry + @command decorator.

Split out of the v0.8.x ``mnemo.cli`` monolith by PR H. Each
``cli/commands/*.py`` module imports :data:`COMMANDS` and the
:func:`command` decorator from here and registers its handler at
import time. The package's ``__init__.py`` triggers those imports so
the registry is populated by the time :func:`mnemo.cli.runtime.main`
looks up a handler.
"""
from __future__ import annotations

import argparse
from typing import Callable

COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {}


def command(name: str) -> Callable:
    def deco(fn: Callable[[argparse.Namespace], int]) -> Callable:
        COMMANDS[name] = fn
        return fn
    return deco


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mnemo", description="The Obsidian that populates itself.")
    sub = p.add_subparsers(dest="command")

    init = sub.add_parser("init", help="first-run setup (idempotent)")
    init.add_argument("--yes", "-y", action="store_true", help="skip prompts (for automation)")
    init.add_argument("--vault-root", type=str, default=None, help="override vault location")
    init.add_argument("--no-mirror", action="store_true", help="skip initial Claude memory mirror")
    init.add_argument("--quiet", action="store_true", help="suppress informational output")

    sub.add_parser("status", help="vault state + hook health + recent activity")
    sub.add_parser("doctor", help="full diagnostic with actionable fixes")
    sub.add_parser("open", help="open vault in Obsidian or file manager")
    sub.add_parser("fix", help="reset circuit breaker")
    extract = sub.add_parser("extract", help="LLM-powered extraction of memory files into shared/_inbox")
    extract.add_argument("--dry-run", action="store_true", help="show what would run without making LLM calls or writes")
    extract.add_argument(
        "--force",
        action="store_true",
        help=(
            "reprocess dismissed and promoted entries. DESTRUCTIVE to "
            "shared/_inbox/<type>/: every .md file in feedback/user/reference "
            "inbox dirs is deleted before the run, wiping prior slug-drift "
            "duplicates. Does not touch shared/_inbox/project/ or sacred dirs."
        ),
    )
    extract.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    briefing = sub.add_parser("briefing", help=argparse.SUPPRESS)
    briefing.add_argument("jsonl_path", type=str)
    briefing.add_argument("agent", type=str)
    sub.add_parser("mcp-server", help=argparse.SUPPRESS)
    sub.add_parser("statusline", help=argparse.SUPPRESS)
    sub.add_parser("statusline-compose", help=argparse.SUPPRESS)
    uninstall = sub.add_parser("uninstall", help="remove hooks (keeps vault)")
    uninstall.add_argument("--yes", "-y", action="store_true")
    telemetry = sub.add_parser("telemetry", help="summarize MCP access log (calls + zero-hit per project)")
    telemetry.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    recall = sub.add_parser("recall", help="measure retrieval ranking vs historical access-log queries")
    recall.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    recall.add_argument("--no-bootstrap", action="store_true", help="reuse existing cases.json instead of regenerating")
    recall.add_argument("--window-s", type=float, default=120.0, help="list→read pair window in seconds (default 120)")
    migrate = sub.add_parser(
        "migrate-worktree-briefings",
        help="move orphan worktree briefings to canonical dir (uses name-prefix heuristic — always --dry-run first)",
    )
    migrate.add_argument(
        "--repos", nargs="+", default=[],
        help="canonical repo paths whose worktree briefings should be relocated",
    )
    migrate.add_argument(
        "--dry-run", action="store_true",
        help="list planned moves without performing them",
    )
    dedup = sub.add_parser(
        "dedup-rules",
        help="merge shared rule files that share the same name (dry-run default)",
    )
    dedup.add_argument(
        "--apply", action="store_true",
        help="execute the plan (default: dry-run)",
    )
    disable = sub.add_parser("disable-rule", help="set runtime: false on a rule's frontmatter by slug")
    disable.add_argument("slug", help="rule slug (from the block message or `mnemo list-enforced`)")
    sub.add_parser("help", help="list commands")
    return p
