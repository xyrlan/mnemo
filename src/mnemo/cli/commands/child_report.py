"""``mnemo child-report`` — hidden, spawned detached by a child's ``SessionEnd``.

Posts the finished child's report card to the session that dispatched it
(#426), then, while that PR's checks are still running and the parent is
still live, waits for them to settle and posts once more. Detached because the
card costs a few ``gh`` calls and the watch costs minutes, and neither may hold
a ``SessionEnd`` open.

It runs ``git`` and ``gh`` and nothing else — never ``claude`` — so it cannot
fire a hook of its own (#329).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from mnemo.cli.parser import command


@command("child-report")
def cmd_child_report(args: argparse.Namespace) -> int:
    import contextlib
    import os

    from mnemo.core import config as cfg_mod, errors as err_mod, paths
    from mnemo.core.sessions import inbox, report_card

    cfg = cfg_mod.load_config()
    vault_root = paths.vault_root(cfg)
    devnull = open(os.devnull, "w", encoding="utf-8")
    try:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            try:
                return _report(args, cfg, vault_root, inbox, report_card)
            except Exception as exc:  # noqa: BLE001 — detached; nobody reads a traceback
                err_mod.log_error(vault_root, "child_report.cli", exc)
                return 1
    finally:
        devnull.close()


def _report(args, cfg, vault_root, inbox, report_card) -> int:
    transcript = Path(args.transcript) if args.transcript else None
    card = report_card.gather(args.short_id, cwd=args.cwd, transcript=transcript)
    minutes = report_card.watch_minutes(cfg)
    name = report_card.state(card)
    delivered = inbox.notify(
        vault_root, args.parent, report_card.render(card, watch_minutes=minutes),
    )
    pr = card.pr.number if card.pr else None
    report_card.record(vault_root, {
        "short_id": args.short_id, "parent": args.parent, "event": "finished",
        "state": name, "pr": pr, "delivered": delivered,
    })
    if not delivered or name != "ci-running" or minutes <= 0:
        return 0

    def alive() -> bool:
        return inbox.is_live(inbox.lookup(vault_root, args.parent))

    sent = []

    def post(text: str) -> bool:
        ok = inbox.notify(vault_root, args.parent, text)
        sent.append(ok)
        return ok

    ended = report_card.watch_checks(card, minutes=minutes, alive=alive, post=post)
    report_card.record(vault_root, {
        "short_id": args.short_id, "parent": args.parent, "event": ended,
        "pr": pr, "delivered": bool(sent and sent[-1]),
    })
    return 0
