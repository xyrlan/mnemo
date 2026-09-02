"""``mnemo learn`` — teach the vault from *this* session, right now.

The hook-driven path already briefs and extracts, but asynchronously, at
session end, behind a debounce. A user who has just corrected Claude wants to
see that the correction landed before they type their next prompt. This verb
runs both stages in the foreground and prints the ledger delta.

The output is the feature. Six lines at most, and each one answers a question
the user would otherwise have to go digging for: what was read, what was
written, what was learned (with the words *they* said as the evidence), what
was held back for review, and where to look next.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mnemo.cli.parser import command


def _tilde(path: Path) -> str:
    """Transcript paths live under $HOME and are long; show them as ``~/…``."""
    try:
        return "~/" + str(Path(path).relative_to(Path.home()))
    except (ValueError, RuntimeError, OSError):
        return str(path)


def _under_vault(path: Path, vault: Path) -> str:
    """Vault-relative when it is in the vault, absolute otherwise."""
    try:
        return str(Path(path).relative_to(vault))
    except ValueError:
        return str(path)


@command("learn")
def cmd_learn(args: argparse.Namespace) -> int:
    """Brief + extract this directory's newest session; print what it taught."""
    from mnemo import cli  # late binding, as every other command does
    from mnemo.core import config as cfg_mod
    from mnemo.core import learn as learn_mod

    cfg = cfg_mod.load_config()
    report = learn_mod.learn(
        cfg,
        cwd=os.getcwd(),
        session_id=getattr(args, "session", None),
        dry_run=bool(getattr(args, "dry_run", False)),
    )

    if report.error:
        print(f"error: {report.error}", file=sys.stderr)
        if learn_mod.LOCK_HELD in report.error:
            print("wait a minute and run `mnemo learn` again", file=sys.stderr)
        return 1

    if report.would_read is not None:
        print(f"would read: {_tilde(report.would_read)}")
        return 0

    if report.transcript is not None:
        print(f"read: {_tilde(report.transcript)}")
    if report.briefing is not None:
        vault = cli._resolve_vault()
        print(
            f"briefing: {_under_vault(report.briefing, vault)} "
            f"({report.corrections} correction(s))"
        )

    if not report.learned:
        if report.hint:
            print(report.hint)
        return 0

    for entry in report.learned:
        line = f"learned: {entry.get('slug')} — {entry.get('name')}"
        # Only a verified rule has a quote the user actually said; an inferred
        # one would otherwise be printed with an empty pair of quotes that
        # reads like the evidence went missing.
        quote = entry.get("quote") or ""
        if entry.get("confidence") == "verified" and quote:
            line += f' (evidence: "{quote}")'
        print(line)

    if report.staged:
        print(f"staged for review: {report.staged} (shared/_inbox/reference/)")

    print("next prompt about this will surface it — check with `mnemo why`")
    return 0
