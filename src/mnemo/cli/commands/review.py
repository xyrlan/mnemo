"""``mnemo review record`` — what a reviewed child decided at its checkpoints (#661).

A child dispatched with the review checkpoints pipes its decisions here as
JSON (the shape is :data:`mnemo.core.review.RECORD_HELP`). One ledger row per
finding goes to ``<vault>/.mnemo/review-ledger.jsonl``, so the reviewer's
precision can be counted later, and the section under ``## Review
checkpoints`` is printed for the child to put in its PR body and its closing
report. Nothing is written into the worktree.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mnemo.cli.parser import command


@command("review")
def cmd_review(args: argparse.Namespace) -> int:
    from mnemo.core import review

    if getattr(args, "action", None) != "record":
        print(review.RECORD_HELP, end="")
        return 1
    if sys.stdin is None or sys.stdin.isatty():
        print("pipe the outcome as JSON on stdin\n\n" + review.RECORD_HELP, end="")
        return 1
    try:
        outcome = review.parse_record(sys.stdin.read())
    except review.RecordError as exc:
        print(f"mnemo review record: {exc}\n\n{review.RECORD_HELP}", end="")
        return 1
    vault = getattr(args, "vault", None)
    if vault is None:
        from mnemo.core import config, paths

        vault = paths.vault_root(config.load_config())
    try:
        review.record(vault, outcome, review.context_of(Path.cwd()))
    except OSError as exc:
        # The section below is still the copy the reader sees; say what is lost.
        print(f"(ledger not written: {exc})", file=sys.stderr)
    print(review.pr_section(outcome), end="")
    return 0
