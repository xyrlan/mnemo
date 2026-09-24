"""``mnemo backfill`` — populate a vault from archived session transcripts.

Two entry shapes:

- ``--install-run``: the capped, current-repo-only sweep that ``session_start``
  spawns once after install. Non-interactive by construction.
- everything else: an explicit sweep the user asks for, which prints an
  estimate and asks before spending.

The install review (#496) drives the second shape from a caller that is not a
person at a terminal: ``--dry-run --json`` prints the estimate as one JSON
document, and ``--yes --extract --progress-json`` runs the sweep and then the
first extraction for that project, printing one JSON line per step. Consent
stays with the caller either way: without ``--yes`` it still asks.

A failure caused by *this transcript* is recorded and stepped over — one
malformed transcript must never abort a sweep. A failure caused by the
*environment* aborts immediately and records nothing; see
:func:`_environmental` for why that asymmetry is load-bearing.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, TextIO

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
    """Entry point. On an install run, owns the one-shot's bookkeeping.

    ``session_start`` launches this and immediately forgets about it — stdout
    and stderr are ``DEVNULL``, because the hook's stdout carries the
    injection envelope. That makes this process the only one that knows
    whether a backfill actually happened, so it is the only one that may say
    so. It marks ``installRunDone`` on a sweep that reached the end, including
    one that had nothing to do; it deliberately does **not** mark an
    environmental abort or an interrupt, so a broken ``claude`` CLI costs the
    user a retry next session rather than the whole feature.
    """
    if not getattr(args, "install_run", False):
        return _run_backfill(args)

    cfg = cfg_mod.load_config()
    vault_root = paths.vault_root(cfg)
    try:
        code = _run_backfill(args)
        if code in (_EXIT_OK, _EXIT_SOME_FAILED) and (cfg.get("backfill") or {}).get(
            "enabled", True
        ):
            # Before the release, not after. `session_start` spawns when it
            # sees no marker *and* wins the lock, so any gap where the lock is
            # gone and the marker is still False is a window for a second
            # sweep — `installCap` more LLM calls, which is the spend the lock
            # exists to prevent. In the opposite order the overlap is a lock
            # held slightly too long, which costs nothing.
            ledger.mark_install_run_done(vault_root)
    finally:
        # Paired with session_start's acquire. A hard kill skips this; the
        # lock is then reaped by its TTL, or — if the marker got written
        # first — retired outright by the next session start, since nothing
        # consults it once the one-shot is spent.
        ledger.release_spawn_lock(vault_root)
    return code


class _Events:
    """The ``--progress-json`` stream: one JSON object per line, flushed.

    Writes to the stdout the command was started with. Everything else the
    run prints — the human lines here, anything extraction prints — is sent
    to stderr while the stream is open, so a caller parsing stdout line by
    line only ever sees these objects.
    """

    def __init__(self, out: TextIO) -> None:
        self._out = out

    def emit(self, event: str, **fields: object) -> None:
        self._out.write(json.dumps({"event": event, **fields}, ensure_ascii=False) + "\n")
        self._out.flush()


def _json_mode(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False) or getattr(args, "progress_json", False))


def _run_backfill(args: argparse.Namespace) -> int:
    if getattr(args, "json", False) and not args.dry_run:
        print("backfill: --json goes with --dry-run; a run reports with --progress-json.",
              file=sys.stderr)
        return _EXIT_ABORTED
    if not getattr(args, "progress_json", False):
        return _run_backfill_inner(args, None)
    events = _Events(sys.stdout)
    with contextlib.redirect_stdout(sys.stderr):
        try:
            return _run_backfill_inner(args, events)
        except Exception as exc:  # noqa: BLE001 — the caller reads exit 2 and this line
            events.emit("error", message=_first_line(exc))
            return _EXIT_ABORTED


def _run_backfill_inner(args: argparse.Namespace, events: Optional[_Events]) -> int:
    cfg = cfg_mod.load_config()
    backfill_cfg = cfg.get("backfill") or {}
    if not backfill_cfg.get("enabled", True):
        if _json_mode(args):
            # Could not run at all: a caller that asked for JSON gets exit 2
            # and a reason, never a sentence where it expected an object.
            message = "backfill: disabled in config (backfill.enabled = false)"
            if events is not None:
                events.emit("error", message=message)
            else:
                print(message, file=sys.stderr)
            return _EXIT_ABORTED
        print("backfill: disabled in config (backfill.enabled = false)")
        return _EXIT_OK

    vault_root = paths.vault_root(cfg)
    candidates = _select(args, cfg)
    led = ledger.load(vault_root)

    if getattr(args, "retry_failed", False):
        # Cleared in memory first, saved only when this is a real run. A
        # dry run that skipped the clear entirely would preview the wrong
        # sweep — retired transcripts would still look ineligible — and one
        # that saved would make `--dry-run` write to the vault, which is the
        # one thing that flag promises it never does.
        cleared = ledger.clear_failed(led)
        noun = "entry" if cleared == 1 else "entries"
        if args.dry_run:
            print(f"backfill: would clear {cleared} failed {noun} — "
                  "previewing the sweep as if they were eligible again.")
        else:
            ledger.save(vault_root, led)
            print(f"backfill: cleared {cleared} failed {noun} — eligible again.")

    todo = [t for t in candidates if ledger.should_harvest(led, t.path)]

    if getattr(args, "json", False):
        # Exactly one JSON document, nothing sent. Always the review run's
        # price — harvest and first extraction — because that is the run a
        # caller asks consent for.
        print(json.dumps(_estimate(todo, cfg, extract=True).as_json(_json_project(args)),
                         ensure_ascii=False))
        return _EXIT_OK

    if not todo:
        _report_nothing_to_do(candidates, led, vault_root)
        if events is not None:
            events.emit("harvest", done=0, of=0)
        if events is not None or getattr(args, "extract", False):
            # Harvested earlier, never extracted: the first extraction is
            # still owed, and it is what a review lists.
            return _then_extract(args, cfg, vault_root, events, harvest_failed=0,
                                 harvest_code=_EXIT_OK)
        return _EXIT_OK

    estimate = _estimate(todo, cfg, extract=bool(getattr(args, "extract", False)))
    projects = sorted({t.agent for t in todo})
    print(
        f"backfill: {len(todo)} session(s) across {len(projects)} project(s): "
        f"{', '.join(projects)}"
    )
    print(
        f"          ~{_fmt_tokens(estimate.input_tokens)} input tokens (rough), "
        f"{estimate.calls} LLM call(s) via your existing claude CLI."
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
            if events is not None:
                events.emit("cancelled")
            return _EXIT_OK

    on_session = None
    if events is not None:
        total = len(todo)

        def on_session(done: int) -> None:
            events.emit("harvest", done=done, of=total)

    code, failed = _sweep(todo, cfg, vault_root, led, on_session=on_session)
    if code not in (_EXIT_OK, _EXIT_SOME_FAILED):
        if events is not None:
            events.emit("error", message="the harvest stopped before the end; see stderr")
        return code
    if events is None and not getattr(args, "extract", False):
        return code
    return _then_extract(args, cfg, vault_root, events, harvest_failed=failed,
                         harvest_code=code)


def _json_project(args: argparse.Namespace) -> str | None:
    """The project the run is scoped to, or None under ``--all``."""
    if args.project is not None:
        return args.project
    if args.all:
        return None
    return _current_project()


def _staged_keys(vault_root: Path) -> set[str]:
    from mnemo.core.filters import is_proposed_sibling, iter_staged_pages

    return {
        f"{p.parent.name}/{p.stem}" for p in iter_staged_pages(Path(vault_root))
        if not is_proposed_sibling(p)
    }


def _live_keys(vault_root: Path) -> set[str]:
    shared = Path(vault_root) / "shared"
    out: set[str] = set()
    if not shared.is_dir():
        return out
    for type_dir in shared.iterdir():
        if not type_dir.is_dir() or type_dir.name.startswith("_"):
            continue
        out.update(f"{type_dir.name}/{p.stem}" for p in type_dir.glob("*.md")
                   if not p.name.endswith(".proposed.md"))
    return out


def _then_extract(
    args: argparse.Namespace,
    cfg: dict,
    vault_root: Path,
    events: Optional[_Events],
    *,
    harvest_failed: int,
    harvest_code: int,
) -> int:
    """The first extraction after a sweep, scoped to the project swept.

    Calls ``run_extraction`` directly — never a hook's ``main``, which would
    bring the hook's own gates and spawns with it. The model calls go through
    ``llm``, whose helpers carry ``MNEMO_HOOKS_OFF`` like every other.

    Without ``--extract`` a ``--progress-json`` run still ends on ``done``,
    with the harvest's counts only.

    ``staged`` and ``live`` are the pages this run added to
    ``shared/_inbox/`` and ``shared/<type>/``: counted from the directories
    before and after, so they are what a review will find, whichever branch
    of the pipeline put them there.
    """
    from mnemo.core import extract as extract_mod

    if not getattr(args, "extract", False):
        if events is not None:
            events.emit("done", staged=0, live=0, failed=harvest_failed)
        return _EXIT_OK if events is not None else harvest_code

    project = _json_project(args)
    staged_before, live_before = _staged_keys(vault_root), _live_keys(vault_root)

    def on_chunk(done: int, total: int) -> None:
        if events is not None:
            events.emit("extract", done=done, of=total)

    try:
        summary = extract_mod.run_extraction(cfg, project=project, on_chunk=on_chunk)
    except KeyboardInterrupt:
        print("\nbackfill: extraction interrupted. `mnemo extract` finishes it.",
              file=sys.stderr)
        return _EXIT_INTERRUPTED
    except Exception as exc:  # noqa: BLE001 — a lock held, a dead CLI, an unwritable vault
        err_mod.log_error(vault_root, "backfill.extract", exc)
        message = f"extraction could not run: {_first_line(exc)}"
        if events is not None:
            events.emit("error", message=message)
        else:
            print(f"backfill: {message}", file=sys.stderr)
        return _EXIT_ABORTED

    staged = len(_staged_keys(vault_root) - staged_before)
    live = len(_live_keys(vault_root) - live_before)
    if events is not None:
        events.emit("done", staged=staged, live=live, failed=harvest_failed,
                    failed_chunks=summary.failed_chunks)
        # Finished, even with failures: they are counted above (#496 spec).
        return _EXIT_OK
    print(f"backfill: extraction staged {staged} page(s) for review, {live} live.")
    if summary.failed_chunks:
        print(f"          ⚠ failed_chunks: {summary.failed_chunks} "
              f"(see {_error_log(vault_root)}; `mnemo extract` retries)", file=sys.stderr)
        return _EXIT_SOME_FAILED
    return harvest_code


def _sweep(
    todo: list[discover.Transcript],
    cfg: dict,
    vault_root: Path,
    led: dict,
    *,
    on_session: Optional[Callable[[int], None]] = None,
) -> tuple[int, int]:
    """Harvest *todo*. Returns ``(exit code, sessions failed)``.

    ``on_session(n)`` is called after each session, harvested or failed, with
    the number of sessions dealt with so far.
    """
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
            return _EXIT_INTERRUPTED, failed
        except Exception as exc:
            err_mod.log_error(vault_root, "backfill.harvest", exc)
            if _environmental(exc):
                aborted = exc
                break
            ledger.mark_failed(led, t.path, str(exc))
            ledger.save(vault_root, led)
            failed += 1
            if on_session is not None:
                on_session(processed + failed)
            continue
        ledger.mark_done(led, t.path, produced=len(written))
        ledger.save(vault_root, led)
        processed += 1
        produced += len(written)
        if not written:
            barren += 1
        if on_session is not None:
            on_session(processed + failed)

    if aborted is not None:
        print(
            f"backfill: stopped — {_first_line(aborted)}\n"
            f"          {processed} session(s) completed first; nothing was held "
            "against the remaining transcripts. Fix the above and rerun to resume.",
            file=sys.stderr,
        )
        return _EXIT_ABORTED, failed

    _report_summary(processed, produced, barren, failed, vault_root)
    return (_EXIT_SOME_FAILED if failed else _EXIT_OK), failed


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


#: Output tokens allowed per harvest call when pricing one. An allowance, not
#: a measurement: a harvest answers with a few pages of JSON.
_HARVEST_OUTPUT_TOKENS = 1_500
#: Input and output tokens allowed per extraction call: a chunk of
#: ``extraction.chunkSize`` memory files plus the existing-rules fragment.
_EXTRACT_CALL_TOKENS = (15_000, 2_000)


@dataclass(frozen=True)
class _Estimate:
    sessions: int
    harvest_calls: int
    extract_calls: int
    input_tokens: int
    api_price_usd: Optional[float]

    @property
    def calls(self) -> int:
        return self.harvest_calls + self.extract_calls

    def as_json(self, project: Optional[str]) -> dict:
        return {
            "project": project,
            "sessions": self.sessions,
            "calls_estimate": self.calls,
            "api_price_estimate_usd": (
                None if self.api_price_usd is None else round(self.api_price_usd, 2)
            ),
            "harvest_calls": self.harvest_calls,
            "extract_calls_estimate": self.extract_calls,
        }


def _estimate(todo: list[discover.Transcript], cfg: dict, *, extract: bool) -> _Estimate:
    """What a sweep of *todo* will send, before anything is sent.

    One figure for the prompt a person answers and the JSON a caller reads.

    A harvest call is made only for a session that clears
    ``backfill.minFileMutations`` — ``harvest_session`` returns before the
    model for the rest — so those are the calls counted, and theirs are the
    input tokens. The extraction cannot be counted before the harvest has
    written anything; with *extract* it is allowed for as one cluster call and
    one reference-gate call per ``extraction.chunkSize`` harvested sessions.

    ``api_price_usd`` is those tokens at the extraction model's API rate from
    :mod:`mnemo.core.pricing` — what the calls would cost on an API key, not a
    charge on a Claude plan — or None when the table has no rate for it.
    """
    from mnemo.core import pricing
    from mnemo.core.briefing import _count_file_mutations, _load_jsonl_events
    from mnemo.core.transcript import flatten_transcript_events

    min_mutations = int((cfg.get("backfill") or {}).get("minFileMutations", 1))
    extraction_cfg = cfg.get("extraction") or {}
    chunk_size = max(1, int(extraction_cfg.get("chunkSize") or 10))
    model = str(extraction_cfg.get("model") or "claude-haiku-4-5")

    harvest_calls = 0
    input_tokens = 0
    for t in todo:
        # _load_jsonl_events swallows OSError and bad lines, so an unreadable
        # transcript is a session that makes no call.
        events = _load_jsonl_events(t.path)
        if _count_file_mutations(events) < min_mutations:
            continue
        harvest_calls += 1
        input_tokens += len(flatten_transcript_events(events)) // 4

    extract_calls = 2 * math.ceil(harvest_calls / chunk_size) if extract else 0
    extract_in, extract_out = _EXTRACT_CALL_TOKENS
    price = pricing.estimate_usd(
        model,
        input_tokens=input_tokens + extract_calls * extract_in,
        output_tokens=harvest_calls * _HARVEST_OUTPUT_TOKENS + extract_calls * extract_out,
    )
    return _Estimate(
        sessions=len(todo),
        harvest_calls=harvest_calls,
        extract_calls=extract_calls,
        input_tokens=input_tokens + extract_calls * extract_in,
        api_price_usd=price,
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
