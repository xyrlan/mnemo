"""The five-minute loop: brief and extract *this* session, synchronously.

Correct Claude once, run ``mnemo learn``, and the rule is live on the next
prompt. The hook-driven path does the same two stages asynchronously, but it
is debounced and fires at session end — too late for a user who wants to see
that their correction landed. ``learn`` runs both stages in the foreground on
the transcript for the current directory, and reports the ledger delta: the
pages this session, and only this session, taught the vault.

Two stages, in order, because the second reads what the first writes:

1. :func:`mnemo.core.briefing.generate_session_briefing` with
   ``min_mutations=0``. A session whose only product is a correction touches
   no files, and the default threshold would skip exactly the session a user
   runs this on. The briefing carries the verified ``## Corrections`` section.
2. :func:`mnemo.core.extract.run_extraction`, which processes dirty files —
   the fresh briefing is what is dirty — runs the evidence gate, rebuilds the
   rule-activation and reflex indexes, and records the learned ledger.

Errors are returned in :class:`LearnReport`, not raised: this is a CLI verb
whose failure modes ("no transcript here yet", "another extraction is already
running") are ordinary conditions the user should read, not tracebacks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mnemo.core import agent as agent_mod
from mnemo.core import learned as learned_mod
from mnemo.core import paths
from mnemo.core.backfill import discover

NO_TRANSCRIPT = (
    "no transcript for this directory yet — start a Claude Code session here first"
)
LOCK_HELD = "another extraction is in progress"
NOTHING_NEW_HINT = (
    "nothing new: no corrections found in this session. A correction is you "
    "telling Claude to stop, change, prefer, or never/always do something — "
    "say it in your own words and run `mnemo learn` again."
)


@dataclass
class LearnReport:
    """What one ``learn`` run read, wrote, and taught the vault."""

    transcript: Optional[Path] = None
    briefing: Optional[Path] = None
    #: Verified corrections in the briefing's frontmatter.
    corrections: int = 0
    #: Ledger entries new since this run started: slug, name, type,
    #: confidence, quote.
    learned: list = field(default_factory=list)
    #: ``summary.demoted_unverified`` — feedback pages staged for review
    #: because their evidence did not verify.
    staged: int = 0
    hint: str = ""
    error: str = ""
    #: Set only under ``dry_run``: the transcript that *would* be read.
    would_read: Optional[Path] = None


def newest_transcript(cwd: str, *, session_id: Optional[str] = None) -> Optional[Path]:
    """The transcript to learn from for ``cwd``, newest first.

    With ``session_id``, the transcript whose filename stem matches it —
    regardless of age, so a user can re-learn an earlier session by id.
    ``None`` when this directory's project has no transcripts, or when no
    stem matches the requested id.
    """
    project = agent_mod.resolve_canonical_agent(cwd).name
    found = discover.find_transcripts(project=project)
    if not found:
        return None
    if session_id is not None:
        for t in found:
            if t.path.stem == session_id:
                return t.path
        return None
    return found[0].path


def _relevant(entry: dict, project: str, universal_threshold: int) -> bool:
    """Entries this project should be told about: its own, plus universals."""
    projects = entry.get("projects") or []
    return project in projects or len(projects) >= universal_threshold


def learn(
    cfg: dict,
    *,
    cwd: str,
    session_id: Optional[str] = None,
    dry_run: bool = False,
) -> LearnReport:
    """Brief and extract this directory's newest session. Never raises."""
    report = LearnReport()

    project = agent_mod.resolve_canonical_agent(cwd).name
    path = newest_transcript(cwd, session_id=session_id)
    if path is None:
        report.error = (
            f"no transcript with session id {session_id} for this directory"
            if session_id is not None
            else NO_TRANSCRIPT
        )
        return report
    report.transcript = path

    if dry_run:
        report.would_read = path
        return report

    vault_root = paths.vault_root(cfg)
    before = learned_mod.max_seq(vault_root)

    # Stage 1 — the briefing. min_mutations=0: the session that earns a
    # `mnemo learn` is the one where the user only *said* something.
    from mnemo.core import briefing as briefing_mod

    report.briefing = briefing_mod.generate_session_briefing(
        path, project, cfg, min_mutations=0
    )
    if report.briefing is not None:
        report.corrections = _count_corrections(report.briefing)

    # Stage 2 — extraction over the dirty files, which now include the
    # briefing just written. The lock is the one expected failure: another
    # extraction (typically the SessionEnd hook's) is already running, and
    # its own pass will pick this briefing up.
    from mnemo.core.extract import run_extraction

    try:
        summary = run_extraction(cfg)
    except Exception as exc:  # noqa: BLE001 — a CLI verb reports, never traces
        message = str(exc)
        report.error = message if LOCK_HELD in message else f"extraction failed: {message}"
        return report

    report.staged = getattr(summary, "demoted_unverified", 0) or 0

    threshold = int((cfg.get("scoping") or {}).get("universalThreshold", 2))
    report.learned = [
        e
        for e in learned_mod._read(vault_root)
        if e.get("seq", 0) > before and _relevant(e, project, threshold)
    ]

    if not report.learned:
        report.hint = NOTHING_NEW_HINT
    return report


def _count_corrections(briefing_path: Path) -> int:
    """The ``corrections:`` count from a briefing's frontmatter. 0 on anything odd."""
    from mnemo.core import filters

    try:
        fm = filters.parse_frontmatter(briefing_path.read_text(encoding="utf-8"))
        return int(fm.get("corrections") or 0)
    except (OSError, TypeError, ValueError):
        return 0
