"""``mnemo backfill`` — populate a vault from archived session transcripts.

Two entry shapes:

- ``--install-run``: the capped, current-repo-only sweep that ``session_start``
  spawns once after install. Non-interactive by construction.
- everything else: an explicit sweep the user asks for, which prints an
  estimate and asks before spending.

A failure caused by *this transcript* is recorded and stepped over — one
malformed transcript must never abort a sweep. A failure caused by the
*environment* aborts immediately and records nothing; see
:func:`_environmental` for why that asymmetry is load-bearing.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mnemo.cli.parser import command
from mnemo.core import config as cfg_mod
from mnemo.core import errors as err_mod
from mnemo.core import paths
from mnemo.core.backfill import discover, harvest, ledger

# Exit codes, mirroring ``extract.py``: 1 = ran but some work failed,
# 2 = could not run, 130 = interrupted.
_EXIT_OK = 0
_EXIT_SOME_FAILED = 1
_EXIT_ABORTED = 2
_EXIT_INTERRUPTED = 130


def _current_project() -> str:
    """Canonical agent name for the cwd."""
    from mnemo.core import agent as agent_mod

    return agent_mod.resolve_canonical_agent(os.getcwd()).name


def _select(args: argparse.Namespace, cfg: dict) -> list[discover.Transcript]:
    """Which transcripts this invocation should consider, newest first."""
    backfill_cfg = cfg.get("backfill") or {}
    if args.install_run:
        return discover.find_transcripts(
            project=_current_project(),
            limit=int(backfill_cfg.get("installCap", 20)),
        )

    project = args.project
    if project is None and not args.all:
        project = _current_project()
    # `is not None`, not truthiness: `--limit 0` is an explicit "no sessions",
    # and reading it as "unlimited" would be the worst possible misreading.
    limit = int(args.limit) if args.limit is not None else None
    return discover.find_transcripts(project=project, limit=limit)


def _environmental(exc: BaseException) -> bool:
    """True when the failure is about the machine, not about this transcript.

    ``LLMSubprocessError`` means no response was ever obtained: the ``claude``
    CLI is missing, auth expired, the account is rate limited, or the
    subprocess timed out twice. In none of those cases did anything judge the
    transcript, so charging it an attempt is simply wrong — and charging
    *every* transcript an attempt, three sweeps running, permanently abandons
    the user's entire history, with the ledger's changed-on-disk escape hatch
    unable to help because archived transcripts never change.

    Timeouts are the ambiguous case: a genuinely enormous transcript can time
    out on its own merits. They are counted as environmental anyway, because
    the two mistakes are not symmetric. Treating an environmental failure as
    transcript-attributable silently and irreversibly poisons the ledger.
    Treating a transcript-attributable failure as environmental stops the sweep
    with a message naming the cause, which the user can route around with
    ``--project`` or ``--limit``. Loud and recoverable beats silent and not.

    Everything else — ``LLMParseError`` (the model answered, but with garbage
    for this input), ``OSError`` on write, anything unforeseen — is treated as
    attributable and consumes the transcript's attempt budget.
    """
    from mnemo.core import llm

    return isinstance(exc, llm.LLMSubprocessError)


@command("backfill")
def cmd_backfill(args: argparse.Namespace) -> int:
    cfg = cfg_mod.load_config()
    backfill_cfg = cfg.get("backfill") or {}
    if not backfill_cfg.get("enabled", True):
        print("backfill: disabled in config (backfill.enabled = false)")
        return _EXIT_OK

    vault_root = paths.vault_root(cfg)
    candidates = _select(args, cfg)
    led = ledger.load(vault_root)
    todo = [t for t in candidates if ledger.should_harvest(led, t.path)]

    if not todo:
        _report_nothing_to_do(candidates, led)
        return _EXIT_OK

    projects = sorted({t.agent for t in todo})
    print(
        f"backfill: {len(todo)} session(s) across {len(projects)} project(s): "
        f"{', '.join(projects)}"
    )
    print(
        f"          ~{_fmt_tokens(_estimate_input_tokens(todo))} input tokens (rough), "
        f"{len(todo)} LLM call(s) via your existing claude CLI."
    )

    if args.dry_run:
        for t in todo:
            print(f"          would harvest {t.agent}/{t.path.name}")
        print("backfill: dry run — nothing written.")
        return _EXIT_OK

    if not args.install_run and not args.yes:
        try:
            reply = input("Proceed? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("y", "yes"):
            print("backfill: cancelled.")
            return _EXIT_OK

    return _sweep(todo, cfg, vault_root, led)


def _sweep(
    todo: list[discover.Transcript],
    cfg: dict,
    vault_root: Path,
    led: dict,
) -> int:
    produced = 0
    processed = 0
    barren = 0
    failed = 0
    aborted: BaseException | None = None

    for t in todo:
        try:
            written = harvest.harvest_session(t.path, t.agent, cfg)
        except KeyboardInterrupt:
            # The ledger is flushed after every session, so the resume promise
            # in this message is true rather than aspirational.
            print(
                f"\nbackfill: interrupted after {processed} session(s). "
                "Rerun to resume where it stopped.",
                file=sys.stderr,
            )
            return _EXIT_INTERRUPTED
        except Exception as exc:
            err_mod.log_error(vault_root, "backfill.harvest", exc)
            if _environmental(exc):
                aborted = exc
                break
            ledger.mark_failed(led, t.path, str(exc))
            ledger.save(vault_root, led)
            failed += 1
            continue
        ledger.mark_done(led, t.path, produced=len(written))
        ledger.save(vault_root, led)
        processed += 1
        produced += len(written)
        if not written:
            barren += 1

    if aborted is not None:
        print(
            f"backfill: stopped — {aborted}\n"
            f"          {processed} session(s) completed first; nothing was held "
            "against the remaining transcripts. Fix the above and rerun to resume.",
            file=sys.stderr,
        )
        return _EXIT_ABORTED

    _report_summary(processed, produced, barren, failed)
    return _EXIT_SOME_FAILED if failed else _EXIT_OK


def _report_summary(processed: int, produced: int, barren: int, failed: int) -> None:
    fruitful = processed - barren
    print(
        f"backfill: processed {processed} session(s), wrote {produced} "
        f"memory file(s) from {fruitful} of them."
    )
    if barren:
        # `harvest_session` returns [] for "quiet session", "below
        # minFileMutations" and "model proposed nothing usable" alike — its
        # docstring is explicit that callers cannot tell them apart — so this
        # line lists the possibilities rather than picking one. Saying it at
        # all matters: without it, `processed 117, wrote 0` reads as 117
        # wasted LLM calls when most of them were never made.
        print(f"          {barren} produced nothing (quiet, below the mutation "
              "threshold, or already on disk).")
    if produced:
        # `extract` is the right next step, but it does not produce live rules
        # from this material: the origin gate stages every backfill-origin page
        # in shared/_inbox/ for a human to confirm, whatever its source count.
        print("          run `mnemo extract` — backfilled pages stage in "
              "shared/_inbox/ for review.")
    if failed:
        print(
            f"          ⚠ failed: {failed} (see ~/.errors.log; re-run to retry)",
            file=sys.stderr,
        )


def _report_nothing_to_do(candidates: list[discover.Transcript], led: dict) -> None:
    """Explain *why* there is nothing to do.

    ``should_harvest`` is False for finished work and for abandoned work alike.
    Reporting both as "already harvested" is the sentence that hides a broken
    environment from the user, so the two populations are counted apart.
    """
    if not candidates:
        print("backfill: no transcripts found for this selection.")
        return
    exhausted = [t for t in candidates if ledger.attempts_exhausted(led, t.path)]
    if not exhausted:
        print("backfill: nothing to do — every transcript is already harvested.")
        return
    done = len(candidates) - len(exhausted)
    print(
        f"backfill: nothing to do — {done} already harvested, {len(exhausted)} "
        f"gave up after {ledger.MAX_ATTEMPTS} failed attempts (see ~/.errors.log)."
    )


def _estimate_input_tokens(transcripts: list[discover.Transcript]) -> int:
    """Input-token estimate for a sweep, measured on what is actually sent.

    Raw transcript bytes are *not* a usable proxy, which an earlier version of
    this function assumed and got wrong by 15-60x on real corpora.
    ``flatten_transcript_events`` reduces each ``tool_use`` block to
    ``[tool_use: <name>]`` — discarding the tool input, where file contents and
    diffs live — and truncates every ``tool_result`` to 400 characters. That is
    the overwhelming majority of the bytes on disk. Quoting the raw figure told
    users a full sweep cost 94M tokens when it costs 6M, which frightens people
    away from the one feature meant to make a cold vault usable.

    So flatten and measure, at ~4 characters per token. Flattening the whole
    902-file corpus this was measured against takes about 1.4s — cheap enough
    that sampling would be a premature optimisation, and the answer is honest.
    """
    from mnemo.core.briefing import _load_jsonl_events
    from mnemo.core.transcript import flatten_transcript_events

    total_chars = 0
    for t in transcripts:
        # _load_jsonl_events swallows OSError and bad lines, so an unreadable
        # transcript contributes 0 rather than exploding the estimate.
        total_chars += len(flatten_transcript_events(_load_jsonl_events(t.path)))
    return total_chars // 4


def _fmt_tokens(n: int) -> str:
    """Round hard. Thousands separators read as a measurement; this is not one."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)
