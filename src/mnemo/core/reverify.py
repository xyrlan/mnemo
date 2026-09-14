"""``mnemo reverify`` — re-brief the sessions behind label-only ``verified``
pages and let today's evidence gate decide (#257).

Sixty ``confidence: verified`` feedback pages on the maintainer's vault were
labelled by ``mnemo reclassify`` on 2026-09-02 against raw transcript turns.
Every one cites a briefing written before the ``## Corrections`` section
existed, so today's gate (:func:`mnemo.core.extract.evidence.page_verifies`)
fails them mechanically — "fails the gate" and "is not a correction" are
different claims, and demoting on the first would drop the three or four
real corrections among them.

So the gate is given the input it lacks. For every label-only page whose
transcript is still on disk, the session is briefed again through the
ordinary briefing path (:func:`mnemo.core.briefing.generate_session_briefing`,
the prompt as it stands today, ``corrections.verify`` against the turns) into
a scratch root, and the page's quote is looked up in the regenerated
``## Corrections``. One LLM call per session, shared by its pages.

Outcomes, per page:

* **verified** — the quote is in the regenerated Corrections, or the
  regenerated item is a tighter span of the same turn (the current prompt
  naming the correction with different boundaries). ``--apply`` rewrites
  ``evidence.source`` to the bare briefing path, narrows the quote to the
  briefing's when the item was the shorter one, and installs the regenerated
  briefing, so ``page_verifies`` passes from then on.
* **fails** — re-briefed, and the quote is still not a correction the
  current prompt would name. ``--apply`` demotes it to a ``reference`` page
  carrying ``demoted_from: feedback``, exactly as ``mnemo reclassify`` does.
* **quote_too_short** — under ``corrections.MIN_CONTENT_TOKENS`` (#119); no
  briefing can back it. Demoted only when its session was re-briefed.
* **no_transcript** — nothing to re-brief; reported, never touched.
* **no_briefing_cited** / **error** — reported, never touched.

Dry run by default: the outcomes are saved to ``.mnemo/reverify-plan.json``
and the regenerated briefings to ``.mnemo/reverify/briefings/``, so
``--apply`` executes what the dry run showed, without a second LLM pass that
could decide differently. The apply step is :func:`mnemo.core.reclassify_apply.apply`
with keep/demote verdicts — same archive, same manifest, same undo — plus the
briefing swap, which :func:`undo` reverses alongside.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from mnemo.core import corrections
from mnemo.core.extract.evidence import page_verifies
from mnemo.core.extract.inbox.rendering import _extract_body
from mnemo.core.reclassify_types import ApplyReport, Plan, Verdict, split_frontmatter

VERIFIED = "verified"
FAILS = "fails"
QUOTE_TOO_SHORT = "quote_too_short"
NO_TRANSCRIPT = "no_transcript"
NO_BRIEFING = "no_briefing_cited"
ERROR = "error"

STATUS_ORDER = (VERIFIED, FAILS, QUOTE_TOO_SHORT, NO_TRANSCRIPT, NO_BRIEFING, ERROR)
_LABELS = {
    VERIFIED: "verified now",
    FAILS: "still fails",
    QUOTE_TOO_SHORT: "quote too short",
    NO_TRANSCRIPT: "no transcript",
    NO_BRIEFING: "no briefing cited",
    ERROR: "briefing error",
}
#: Statuses ``--apply`` demotes: the session was re-briefed and the page is still unbacked.
DEMOTED_STATUSES = frozenset({FAILS, QUOTE_TOO_SHORT})

PLAN_NAME = "reverify-plan.json"
SCRATCH_DIR = "reverify"

# ``bots/<agent>/briefings/sessions/<sid>.md`` wherever it sits in the string:
# reclassify wrote ``briefing: <path> — user turns, turn 3``.
_CITATION = re.compile(r"bots/([^/\s]+)/briefings/sessions/([^/\s]+?)\.md")

Briefer = Callable[[Path, str, str, Path], Path]


@dataclass(frozen=True)
class Candidate:
    slug: str
    path: str  # vault-relative rule file
    quote: str
    link: str
    briefing_rel: str  # "" when the evidence cites no briefing
    agent: str
    session_id: str


@dataclass(frozen=True)
class Outcome:
    slug: str
    path: str
    quote: str
    link: str
    briefing_rel: str
    agent: str
    session_id: str
    status: str
    detail: str = ""


@dataclass
class Report:
    run_id: str
    outcomes: list
    llm_calls: int = 0
    generated_at: str = ""
    # Sessions whose regenerated briefing sits in scratch: session id → vault-relative path.
    rebriefed: dict = field(default_factory=dict)

    def counts(self) -> dict:
        out: dict = {}
        for o in self.outcomes:
            out[o.status] = out.get(o.status, 0) + 1
        return out


# --- what to re-verify ---------------------------------------------------------

def candidates(vault_root: Path) -> list[Candidate]:
    """Every consumer-visible ``shared/feedback/`` page that is
    ``confidence: verified`` with a quote and does **not** pass today's gate —
    the label-only half of ``mnemo replay``'s split."""
    from mnemo.core.filters import derive_rule_slug, is_consumer_visible

    vault_root = Path(vault_root)
    out: list[Candidate] = []
    for md in sorted((vault_root / "shared" / "feedback").glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, _ = split_frontmatter(text)
        if not is_consumer_visible(md, fm, vault_root):
            continue
        if str(fm.get("confidence") or "") != "verified":
            continue
        evidence = fm.get("evidence")
        if not isinstance(evidence, dict):
            continue
        quote = str(evidence.get("quote") or "").strip()
        if not quote:
            continue
        sources_raw = fm.get("sources") or []
        if isinstance(sources_raw, str):
            sources_raw = [sources_raw]
        sources = [s for s in sources_raw if isinstance(s, str)]
        if page_verifies(evidence, sources, vault_root):
            continue
        m = _CITATION.search(str(evidence.get("source") or ""))
        out.append(Candidate(
            slug=derive_rule_slug(fm, md.stem),
            path=md.relative_to(vault_root).as_posix(),
            quote=quote,
            link=str(evidence.get("link") or ""),
            briefing_rel=m.group(0) if m else "",
            agent=m.group(1) if m else "",
            session_id=m.group(2) if m else "",
        ))
    return out


# --- the dry run -----------------------------------------------------------------

def default_briefer(cfg: dict, *, fresh: bool = False) -> Briefer:
    """The ordinary briefing path, pointed at a scratch vault root so the
    vault's own briefings are not touched by a dry run.

    A scratch briefing whose ``transcript_sha256`` still matches is reused,
    so a rerun re-buckets the briefings the user already inspected without
    a second LLM pass that could decide differently; ``fresh`` forces one.
    """
    from mnemo.core import briefing as briefing_mod

    def _brief(jsonl: Path, agent: str, session_id: str, out_root: Path) -> Path:
        scratch_cfg = dict(cfg)
        scratch_cfg["vaultRoot"] = str(out_root)
        written = briefing_mod.generate_session_briefing(
            jsonl, agent, scratch_cfg, min_mutations=0, reuse_unchanged=not fresh,
        )
        if written is None:
            raise RuntimeError("briefing skipped")
        return written

    return _brief


def _outcome(c: Candidate, status: str, detail: str = "", *, quote: Optional[str] = None) -> Outcome:
    fields = asdict(c)
    if quote is not None:
        fields["quote"] = quote
    return Outcome(**fields, status=status, detail=detail)


def _match(page_quote: str, items: list) -> Optional[tuple]:
    """(item, quote to keep) when the regenerated Corrections back the page.

    Today's gate reads page-quote ⊆ item-quote. The re-brief may quote the
    same turn with tighter boundaries — item-quote ⊆ page-quote — which is
    the same correction named again; then the page keeps the briefing's
    span, itself held to ``quote_is_specific``, so the gate passes tomorrow.
    """
    for it in items:
        if corrections.quote_matches_turn(page_quote, it.quote):
            return it, page_quote
    for it in items:
        if corrections.quote_matches_turn(it.quote, page_quote) and corrections.quote_is_specific(it.quote):
            return it, it.quote
    return None


def run(
    vault_root: Path,
    *,
    scratch: Path,
    briefer: Briefer,
    projects_root: Optional[Path] = None,
    run_id: Optional[str] = None,
) -> Report:
    """Re-brief each session once and bucket every candidate. Writes only
    under *scratch*."""
    from mnemo.core.mcp.recall_sessions import _transcripts_by_session

    vault_root = Path(vault_root)
    if projects_root is None:
        projects_root = Path.home() / ".claude" / "projects"
    by_session = _transcripts_by_session(Path(projects_root))
    scratch_briefings = Path(scratch) / "briefings"
    scratch_briefings.mkdir(parents=True, exist_ok=True)

    report = Report(
        run_id=run_id or datetime.now().strftime("%Y%m%dT%H%M%S"),
        outcomes=[],
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )

    groups: dict = {}
    for c in candidates(vault_root):
        if not c.briefing_rel:
            report.outcomes.append(_outcome(c, NO_BRIEFING))
            continue
        groups.setdefault((c.agent, c.session_id), []).append(c)

    for (agent, sid), pages in groups.items():
        jsonl = by_session.get(sid)
        if jsonl is None:
            report.outcomes.extend(_outcome(c, NO_TRANSCRIPT) for c in pages)
            continue
        try:
            written = briefer(Path(jsonl), agent, sid, scratch_briefings)
            report.llm_calls += 1
            items = corrections.parse_section(_extract_body(written.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001 — one bad session must not sink the run
            report.outcomes.extend(_outcome(c, ERROR, f"{type(exc).__name__}: {exc}") for c in pages)
            continue
        report.rebriefed[sid] = pages[0].briefing_rel
        for c in pages:
            if not corrections.quote_is_specific(c.quote):
                report.outcomes.append(_outcome(
                    c, QUOTE_TOO_SHORT, f"under {corrections.MIN_CONTENT_TOKENS} content words"))
                continue
            hit = _match(c.quote, items)
            if hit is not None:
                item, keep_quote = hit
                narrowed = "" if keep_quote == c.quote else " (quote narrowed to the briefing's span)"
                report.outcomes.append(_outcome(c, VERIFIED, f"→ {item.rule}{narrowed}", quote=keep_quote))
            else:
                report.outcomes.append(_outcome(
                    c, FAILS, f"{len(items)} correction(s) in the new briefing, none carries the quote"))
    return report


# --- the saved plan --------------------------------------------------------------

def save_report(report: Report, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": report.run_id,
        "generated_at": report.generated_at,
        "llm_calls": report.llm_calls,
        "rebriefed": report.rebriefed,
        "outcomes": [asdict(o) for o in report.outcomes],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_report(path: Path) -> Report:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return Report(
        run_id=str(raw["run_id"]),
        outcomes=[Outcome(**o) for o in raw.get("outcomes") or []],
        llm_calls=int(raw.get("llm_calls") or 0),
        generated_at=str(raw.get("generated_at") or ""),
        rebriefed=dict(raw.get("rebriefed") or {}),
    )


# --- apply and undo --------------------------------------------------------------

def _archive_dir(vault_root: Path, run_id: str) -> Path:
    return Path(vault_root) / "shared" / "_archive" / f"reclassify-{run_id}"


def apply(vault_root: Path, report: Report, *, scratch: Path) -> ApplyReport:
    """Execute the saved dry run: keep what verified (bare source path, the
    regenerated briefing installed), demote what was re-briefed and still
    fails. Everything else is left exactly as it was."""
    from mnemo.core import reclassify_apply as RA

    vault_root = Path(vault_root)
    verdicts: list = []
    for o in report.outcomes:
        if o.status == VERIFIED:
            verdicts.append(Verdict(slug=o.slug, verdict="keep", quote=o.quote,
                                    source=o.briefing_rel, link=o.link, path=o.path))
        elif o.status in DEMOTED_STATUSES:
            verdicts.append(Verdict(slug=o.slug, verdict="demote", path=o.path,
                                    reason=_LABELS[o.status]))

    arch = _archive_dir(vault_root, report.run_id)
    if (arch / "manifest.json").exists():
        raise RuntimeError(f"run {report.run_id} already applied; undo it first")

    # Install the regenerated briefing for every session that backed a page:
    # the keep rewrites ``evidence.source`` to the bare path, and the gate must
    # find the quote there tomorrow. Originals go next to reclassify's.
    swaps: list = []
    needed = {o.session_id for o in report.outcomes if o.status == VERIFIED}
    for sid in sorted(needed):
        rel = report.rebriefed.get(sid)
        fresh = Path(scratch) / "briefings" / rel if rel else None
        if not rel or fresh is None or not fresh.is_file():
            continue
        live = vault_root / rel
        backup = arch / "originals" / "briefings" / rel
        backup.parent.mkdir(parents=True, exist_ok=True)
        if live.exists():
            shutil.copy2(live, backup)
        live.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fresh, live)
        swaps.append({"path": rel, "original": backup.relative_to(vault_root).as_posix()
                      if backup.exists() else None})

    result = RA.apply(vault_root, Plan(run_id=report.run_id, llm_calls=report.llm_calls, verdicts=verdicts))
    (arch / "briefings.json").write_text(json.dumps({"swaps": swaps}, indent=2), encoding="utf-8")
    return result


def undo(vault_root: Path, run_id: str) -> int:
    """reclassify's byte-exact undo, plus the briefing swap. Returns files restored."""
    from mnemo.core import reclassify_apply as RA

    vault_root = Path(vault_root)
    restored = RA.undo(vault_root, run_id)
    sidecar = _archive_dir(vault_root, run_id) / "briefings.json"
    if not sidecar.exists():
        return restored
    for swap in (json.loads(sidecar.read_text(encoding="utf-8")).get("swaps") or []):
        live = vault_root / str(swap.get("path") or "")
        original = swap.get("original")
        if original and (vault_root / original).exists():
            live.write_bytes((vault_root / original).read_bytes())
            restored += 1
        elif live.exists():
            live.unlink()
            restored += 1
    return restored


# --- the report ------------------------------------------------------------------

def format_report(report: Report, *, applied: Optional[ApplyReport] = None) -> str:
    counts = report.counts()
    lines = ["mnemo reverify — label-only verified rules, re-briefed and re-gated", ""]
    lines.append(f"pages checked   {len(report.outcomes):>4}     sessions re-briefed   {report.llm_calls}")
    for status in STATUS_ORDER:
        if counts.get(status):
            lines.append(f"  {_LABELS[status]:<20}{counts[status]:>4}")
    for status in STATUS_ORDER:
        rows = [o for o in report.outcomes if o.status == status]
        if not rows:
            continue
        lines.append("")
        lines.append(_LABELS[status])
        for o in rows:
            quote = " ".join(o.quote.split())
            if len(quote) > 60:
                quote = quote[:60] + "…"
            where = o.session_id[:8] if o.session_id else "-"
            lines.append(f'  {o.slug}  [{where}]  "{quote}"')
            if o.detail:
                lines.append(f"      {o.detail}")
    lines.append("")
    if applied is None:
        demote = sum(counts.get(s, 0) for s in DEMOTED_STATUSES)
        lines.append(f"dry run — nothing changed. --apply would keep {counts.get(VERIFIED, 0)} "
                     f"(bare source path, briefing installed) and demote {demote}; the rest stay as they are.")
    else:
        lines.append(f"applied: kept {applied.kept}, demoted {applied.demoted}"
                     + (f", skipped {len(applied.skipped)}" if applied.skipped else ""))
        lines.append(f"undo with: mnemo reverify --undo {report.run_id}")
    return "\n".join(lines)
