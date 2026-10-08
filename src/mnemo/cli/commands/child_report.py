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
import contextlib
import time
from pathlib import Path

from mnemo.cli.parser import command


@command("child-report")
def cmd_child_report(args: argparse.Namespace) -> int:
    import os

    from mnemo.core import config as cfg_mod, errors as err_mod, paths
    from mnemo.core.sessions import inbox, report_card

    cfg = cfg_mod.load_config()
    vault_root = paths.vault_root(cfg)
    # Every row this process writes names it, so ``mnemo doctor`` can pair the
    # hook's ``spawned`` row with what came of it even when this row lands
    # first (#460).
    base = {"short_id": args.short_id, "parent": args.parent, "reporter": os.getpid()}
    devnull = open(os.devnull, "w", encoding="utf-8")
    try:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull), \
                _signals_raise():
            try:
                return _report(args, cfg, vault_root, base, inbox, report_card)
            except (Exception, Killed) as exc:  # noqa: BLE001 — detached; nobody reads a traceback
                err_mod.log_error(vault_root, "child_report.cli", exc)
                # A crash leaves a row too (#454): a missing row is not a symptom
                # anyone can read. So does a catchable signal (#460).
                why = (f"child-report killed by {exc}" if isinstance(exc, Killed)
                       else f"child-report crashed: {type(exc).__name__}: {exc}")
                report_card.record(vault_root, {
                    **base, "event": "finished", "delivered": False, "reason": why,
                })
                return 1
    finally:
        devnull.close()


class Killed(BaseException):
    """A signal that would otherwise end the reporter without a row (#460).

    A ``BaseException``, as ``KeyboardInterrupt`` is, so the ``except
    Exception`` that keeps :func:`report_card.gather` from failing the notice
    cannot swallow it into a card.
    """


@contextlib.contextmanager
def _signals_raise():
    """Turn SIGTERM, SIGHUP and SIGINT into :class:`Killed` while reporting.

    Their default action ends the process before any ``except`` runs, so a
    reporter stopped that way used to leave only the hook's ``spawned`` row.
    SIGKILL cannot be caught; ``mnemo doctor`` is what names that one. The
    previous handlers come back on the way out.
    """
    import signal

    def _raise(signum, _frame):
        raise Killed(signal.Signals(signum).name)

    saved = {}
    for name in ("SIGTERM", "SIGHUP", "SIGINT"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            saved[signum] = signal.signal(signum, _raise)
        except (OSError, ValueError):  # not the main thread, or not settable here
            pass
    try:
        yield
    finally:
        for signum, handler in saved.items():
            signal.signal(signum, handler)


def _report(args, cfg, vault_root, base, inbox, report_card) -> int:
    transcript = Path(args.transcript) if args.transcript else None
    card = report_card.gather(args.short_id, cwd=args.cwd, transcript=transcript)
    minutes = report_card.watch_minutes(cfg)
    name = report_card.state(card)
    text = report_card.render(card, watch_minutes=minutes)
    why = inbox.deliver(vault_root, args.parent, text)
    pr = card.pr.number if card.pr else None
    row = {**base, "event": "finished", "state": name, "pr": pr}
    if why:
        # Held for the parent's next prompt (#586). Without a live parent
        # there is no watch, so the card as rendered now is the whole notice.
        report_card.undelivered(vault_root, row, why, text=text)
        return 0
    report_card.record(vault_root, {**row, "delivered": True})
    if name != "ci-running" or minutes <= 0:
        return 0

    def alive() -> bool:
        return inbox.resolve(vault_root, args.parent)[0] is not None

    sent = []
    failed = []

    def post(text: str) -> bool:
        ok = inbox.notify(vault_root, args.parent, text)
        sent.append(ok)
        if not ok:
            failed.append(text)
        return ok

    from mnemo.core.sessions import pr_follow

    started = time.time()

    def stand_down() -> bool:
        return pr_follow.woken_since(vault_root, args.short_id, pr=pr, since=started)

    ended = report_card.watch_checks(card, minutes=minutes, alive=alive, post=post,
                                     stand_down=stand_down)
    row = {**base, "event": ended, "pr": pr}
    if ended == "stood-down":
        # pr-follow woke the child for this PR (#623): its next card reports.
        report_card.record(vault_root, {
            **row, "delivered": False,
            "reason": "pr-follow woke the child; its next card reports",
        })
        return 0
    if sent and sent[-1]:
        report_card.record(vault_root, {**row, "delivered": True})
    else:
        report_card.undelivered(
            vault_root, row,
            "parent not live when checks were due" if not sent else "socket write failed",
            text=failed[-1] if failed else None,
        )
    return 0
