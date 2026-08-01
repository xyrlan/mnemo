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

    Environmental failures are identical for every input, so charging one an
    attempt is wrong, and charging *all* of them an attempt — three sweeps
    running — permanently abandons the user's entire history, since archived
    transcripts never change and the ledger's changed-hash escape hatch can
    never fire. Those abort the sweep instead, recording nothing.

    Attributable failures are properties of the input. They consume that
    transcript's budget and the sweep steps over them, because a sweep that
    stops dead on one hostile transcript can never reach the ones behind it:
    ``find_transcripts`` applies ``limit`` as a newest-first *prefix*, so no
    ``--limit`` value skips past a wedge, and ``--project`` only helps when the
    wedge happens to sit in a different project from the remaining work —
    which the biggest, most timeout-prone transcripts do not.

    The split cannot be inferred from exception type alone: ``llm.call``
    overloads two types across four unrelated conditions. It is drawn where
    the errors are raised, via :class:`llm.LLMTimeoutError` and
    :class:`llm.LLMEnvelopeError`.

    environmental — missing CLI, failed auth, rate limit (``LLMSubprocessError``);
        malformed CLI envelope (``LLMEnvelopeError``: a login nag, an update
        notice, a PATH shim, an ``--output-format`` change).
    attributable — double timeout (``LLMTimeoutError``: scales with prompt
        size, so usually an oversized transcript); content-level parse garbage
        (``LLMParseError`` from ``harvest``'s own ``_parse_llm_json`` call);
        ``OSError`` on write; anything unforeseen.
    """
    from mnemo.core import llm

    if isinstance(exc, llm.LLMTimeoutError):
        return False  # checked first: it is an LLMSubprocessError subclass
    return isinstance(exc, (llm.LLMSubprocessError, llm.LLMEnvelopeError))


def _first_line(exc: BaseException, limit: int = 200) -> str:
    """One tidy line from an exception.

    ``LLMSubprocessError`` embeds the subprocess's ``stderr``, which for a
    Node-based CLI can be a multi-line stack trace. Splattering that across
    the abort message buries the sentence that tells the user what to do.
    """
    text = " ".join(str(exc).splitlines()).strip() or type(exc).__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"


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

    if getattr(args, "retry_failed", False):
        cleared = ledger.clear_failed(led)
        ledger.save(vault_root, led)
        noun = "entry" if cleared == 1 else "entries"
        print(f"backfill: cleared {cleared} failed {noun} — eligible again.")

    todo = [t for t in candidates if ledger.should_harvest(led, t.path)]

    if not todo:
        _report_nothing_to_do(candidates, led, vault_root)
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
            f"backfill: stopped — {_first_line(aborted)}\n"
            f"          {processed} session(s) completed first; nothing was held "
            "against the remaining transcripts. Fix the above and rerun to resume.",
            file=sys.stderr,
        )
        return _EXIT_ABORTED

    _report_summary(processed, produced, barren, failed, vault_root)
    return _EXIT_SOME_FAILED if failed else _EXIT_OK


def _error_log(vault_root: Path) -> Path:
    """Where ``errors.log_error`` actually writes — not ``~/.errors.log``.

    The log lives in the vault, which defaults to ``~/mnemo``. Pointing a
    brand-new user at a path that does not exist, on the first command they
    ever run, is a bad first impression to inherit.
    """
    return Path(vault_root) / err_mod.ERROR_LOG_NAME


def _report_summary(
    processed: int, produced: int, barren: int, failed: int, vault_root: Path
) -> None:
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
            f"          ⚠ failed: {failed} (see {_error_log(vault_root)}; "
            "re-run to retry)",
            file=sys.stderr,
        )


def _report_nothing_to_do(
    candidates: list[discover.Transcript], led: dict, vault_root: Path
) -> None:
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
        f"gave up after {ledger.MAX_ATTEMPTS} failed attempts "
        f"(see {_error_log(vault_root)})."
    )
    # The remedy belongs where the problem is reported: without it the only
    # way out of a terminal `attempts >= 3` is hand-editing backfill-state.json.
    print("          `mnemo backfill --retry-failed` clears them for another try.")


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
