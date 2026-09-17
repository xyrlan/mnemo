"""The retroactive friction sweep — recover the corrections the vault threw away.

    mnemo friction --backfill     # dry run: re-brief, verify, link → .mnemo/friction-backfill-plan.json
    mnemo friction --apply        # write exactly what the dry run holds (no LLM)

Every session with a transcript on disk is swept, newest first — not only the
356 briefings that predate the ``## Corrections`` prompt. A session that never
produced a briefing can still have contradicted a rule, and restricting the
sweep to briefed sessions would inherit the sampling bias the ledger exists to
remove. Sessions the vault has a briefing for but whose transcript is gone are
listed too, as *transcript gone*, and nothing is fabricated for them.

Per session, :func:`plan`:

0. sets aside a session whose ``cwd`` is a background job's scratch dir
   (:func:`mnemo.core.corrections.is_job_scratch`) as *scratch session*,
   without a briefing: every turn in it was written by another session probing
   the harness, and the contradiction pass would otherwise link "do nothing
   else" to a rule the user still wants (#348);
1. re-briefs the transcript through the ordinary path
   (:func:`mnemo.core.briefing.generate_session_briefing`) into a scratch root,
   one Haiku call, reused on a rerun unless ``fresh`` — the shape
   ``mnemo reverify`` (#257) established;
2. parses ``## Corrections`` and checks every quote with
   :func:`mnemo.core.corrections.verify` against the turns the user typed, so a
   fabricated quote never reaches the plan — let alone the ledger;
3. ranks the vault against each surviving correction and asks the
   contradiction pass which candidates it contradicts (``rank`` + ``resolve``
   from the ``link`` piece). A failure there costs the link, never the
   correction.

The plan is saved after every session, so an interrupted sweep keeps what it
already planned, and a rerun reuses those sessions instead of paying for them
twice. :func:`apply` then writes the plan to the ledger with
``backfilled: true`` and runs no second briefing or contradiction pass — what
the dry run showed is what lands. :func:`mnemo.core.friction.ledger.record`
refuses a ``(session, quote)`` it already holds, so ``--apply`` twice writes
each row once.

**Why a rank sidecar.** ``mnemo friction`` must report the rank at which each
confirmed link sat among the candidates, so ``CONTRADICTION_CANDIDATES`` can be
revisited against evidence. The ledger's record shape is fixed (wave 1) and
carries no rank, so :func:`apply` writes one ``{id, slug, rank}`` row per link
to ``.mnemo/friction-link-ranks.jsonl`` beside it. Same switch, same rotation,
never raises.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from mnemo.core import corrections
from mnemo.core.friction import ledger

PLAN_NAME = "friction-backfill-plan.json"
SCRATCH_DIR = "friction-backfill"
LINK_RANKS_NAME = "friction-link-ranks.jsonl"

FOUND = "corrections_found"
NONE_FOUND = "none_found"
GONE = "transcript_gone"
FAILED = "briefing_failed"
#: The session ran in a background job's scratch dir: a probe, not the user (#348).
SCRATCH = "scratch_session"

STATUS_ORDER = (FOUND, NONE_FOUND, GONE, FAILED, SCRATCH)
LABELS = {
    FOUND: "corrections found",
    NONE_FOUND: "none found",
    GONE: "transcript gone",
    FAILED: "briefing failed",
    SCRATCH: "scratch session",
}

#: ``(transcript, project, session_id, out_root) -> briefing path``; raises on failure.
Briefer = Callable[[Path, str, str, Path], Path]
Progress = Callable[["SessionPlan"], None]


@dataclass
class PlannedCorrection:
    quote: str
    rule: str
    contradicts: list = field(default_factory=list)
    #: slug → 1-based position among the candidates the pass was shown.
    ranks: dict = field(default_factory=dict)
    link_basis: str = ledger.LINK_NONE
    candidates: int = 0
    #: Why the link is empty when it failed rather than found nothing.
    detail: str = ""


@dataclass
class SessionPlan:
    session_id: str
    project: str
    status: str
    detail: str = ""
    #: When the session started, ``YYYY-MM-DDTHH:MM:SSZ``; the ledger row's ``ts``.
    ts: str = ""
    #: Vault-relative path of the scratch briefing the corrections were read from.
    briefing: str = ""
    transcript_sha256: str = ""
    injected: list = field(default_factory=list)
    corrections: list = field(default_factory=list)
    #: Quotes the briefing proposed that the user never typed.
    rejected: int = 0

    @property
    def found(self) -> int:
        return len(self.corrections)


@dataclass
class BackfillPlan:
    run_id: str
    generated_at: str = ""
    since: Optional[str] = None
    project: Optional[str] = None
    fresh: bool = False
    #: False while the sweep is running, and after an interrupted one.
    complete: bool = False
    briefing_calls: int = 0
    link_calls: int = 0
    sessions: list = field(default_factory=list)

    def counts(self) -> dict:
        out = {s: 0 for s in STATUS_ORDER}
        for s in self.sessions:
            out[s.status] = out.get(s.status, 0) + 1
        return out

    @property
    def corrections(self) -> int:
        return sum(s.found for s in self.sessions)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "BackfillPlan":
        sessions = []
        for s in raw.get("sessions") or []:
            s = dict(s)
            s["corrections"] = [PlannedCorrection(**c) for c in s.get("corrections") or []]
            sessions.append(SessionPlan(**s))
        fields = {k: raw[k] for k in (
            "run_id", "generated_at", "since", "project", "fresh",
            "complete", "briefing_calls", "link_calls",
        ) if k in raw}
        return cls(sessions=sessions, **fields)


@dataclass
class BackfillReport:
    run_id: str
    #: Ledger ids appended by this apply.
    written: list = field(default_factory=list)
    #: Corrections the ledger already held — a rerun, not a loss.
    duplicates: int = 0
    #: Corrections that should have been written and were not (see ``.errors.log``).
    failed: int = 0
    sessions: int = 0
    #: True when telemetry is switched off: the ledger follows that switch, so nothing was written.
    telemetry_off: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# --- paths ---------------------------------------------------------------------

def plan_path(vault_root: Path) -> Path:
    return Path(vault_root) / ".mnemo" / PLAN_NAME


def scratch_root(vault_root: Path) -> Path:
    return Path(vault_root) / ".mnemo" / SCRATCH_DIR


def link_ranks_path(vault_root: Path) -> Path:
    return Path(vault_root) / ".mnemo" / LINK_RANKS_NAME


def save_plan(p: BackfillPlan, path: Path) -> None:
    """Atomic: an interrupt mid-write must not leave a torn plan behind."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(p.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_plan(path: Path) -> BackfillPlan:
    return BackfillPlan.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# --- the seams -----------------------------------------------------------------

def default_briefer(cfg: dict, *, fresh: bool = False) -> Briefer:
    """The ordinary briefing path, into a scratch vault root.

    ``min_mutations=0``: a session whose only product is a correction touches
    no files, and it is exactly the session this sweep is for.
    """
    from mnemo.core.reverify import default_briefer as _reverify_briefer

    return _reverify_briefer(cfg, fresh=fresh)


def _default_ranker() -> Callable:
    try:
        from mnemo.core.friction.candidates import rank
    except ImportError:  # the link piece may expose it from link.py
        from mnemo.core.friction.link import rank  # type: ignore[no-redef]
    return rank


def _default_resolver() -> Callable:
    from mnemo.core.friction.link import resolve

    return resolve


# --- which sessions --------------------------------------------------------------

def _since_bound(since: Any) -> Optional[str]:
    if since is None:
        return None
    if isinstance(since, date):
        return since.strftime("%Y-%m-%d")
    text = str(since).strip()[:10]
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"since must be a date or 'YYYY-MM-DD', got {since!r}") from None
    return text


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""


def _briefed_sessions(vault_root: Path) -> dict:
    """session id → (project, briefing path), from the vault's own briefings."""
    out: dict = {}
    for md in sorted(Path(vault_root).glob("bots/*/briefings/sessions/*.md")):
        out.setdefault(md.stem, (md.parents[2].name, md))
    return out


def _project_for(path: Path, briefed: Optional[tuple], cwd: Optional[str]) -> str:
    """The briefing's record of the project first — it was resolved while the
    tree still existed — then the transcript's own ``cwd`` (#301), then the
    project directory's name."""
    if briefed is not None:
        return briefed[0]
    from mnemo.core.backfill import discover

    if cwd:
        try:
            return discover.agent_for_cwd(cwd)
        except Exception:
            pass
    return discover._fallback_agent(path.parent.name)


def _briefing_day(md: Path) -> str:
    try:
        from mnemo.core.extract.scanner import parse_frontmatter

        fm, _ = parse_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
        day = str(fm.get("date") or "")[:10]
        if day:
            return day
    except Exception:
        pass
    return _day(_mtime(md))


def _injected_by_session(vault_root: Path) -> dict:
    """session id → slugs the reflex injected into it, in first-seen order."""
    from mnemo.core.log_utils import iter_rotated_rows

    out: dict = {}
    for row in iter_rotated_rows(Path(vault_root) / ".mnemo" / "reflex-log.jsonl"):
        sid = row.get("session_id")
        emitted = row.get("emitted")
        if not isinstance(sid, str) or not isinstance(emitted, list):
            continue
        seen = out.setdefault(sid, [])
        for slug in emitted:
            if isinstance(slug, str) and slug not in seen:
                seen.append(slug)
    return out


def _utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _session_start(events: list) -> str:
    from mnemo.core.briefing import _parse_timestamp

    times = [t for t in (_parse_timestamp(ev.get("timestamp")) for ev in events) if t is not None]
    return _utc(min(times)) if times else ""


# --- the dry run -----------------------------------------------------------------

def _link(
    vault_root: Path,
    item: corrections.Correction,
    *,
    project: str,
    injected: list,
    ranker: Optional[Callable],
    resolver: Optional[Callable],
    runner: Any,
) -> PlannedCorrection:
    """Rank and judge one correction. Never raises: a failed link — the pass
    erred, or the ``link`` piece is not installed — is an unlinked correction,
    never a lost one."""
    out = PlannedCorrection(quote=item.quote, rule=item.rule)
    try:
        ranker = ranker or _default_ranker()
        resolver = resolver or _default_resolver()
        cands = list(ranker(vault_root, item, project=project))
        out.candidates = len(cands)
        positions: dict = {}
        for i, c in enumerate(cands, start=1):
            positions.setdefault(c.slug, int(getattr(c, "rank", 0) or i))
        result = resolver(item, cands, runner=runner)
        # A slug the model names outside the list it was shown is dropped —
        # the ledger records only links to rules the pass actually read.
        slugs: list = []
        for slug in getattr(result, "contradicts", None) or []:
            if slug in positions and slug not in slugs:
                slugs.append(slug)
        out.contradicts = slugs
        out.ranks = {s: positions[s] for s in slugs}
        if not slugs:
            out.link_basis = ledger.LINK_NONE
        elif set(slugs) & set(injected):
            out.link_basis = ledger.LINK_EXTRACTOR_INJECTED
        else:
            out.link_basis = ledger.LINK_EXTRACTOR
    except Exception as exc:  # noqa: BLE001
        out.contradicts, out.ranks, out.link_basis = [], {}, ledger.LINK_NONE
        out.detail = f"link failed: {type(exc).__name__}: {exc}"
    return out


def plan(
    vault_root: Path,
    *,
    since: Any = None,
    project: Optional[str] = None,
    fresh: bool = False,
    projects_root: Optional[Path] = None,
    briefer: Optional[Briefer] = None,
    ranker: Optional[Callable] = None,
    resolver: Optional[Callable] = None,
    runner: Any = None,
    progress: Optional[Progress] = None,
) -> BackfillPlan:
    """Sweep every session, newest first, and save the plan as it grows.

    ``since`` (a date or ``YYYY-MM-DD``) keeps sessions whose transcript was
    last written on or after it — the file's mtime, read before anything is
    parsed, so a bounded run never opens what it skips. A gone session is
    dated by its briefing. ``project`` matches the resolved project exactly.

    A session the previous plan already holds for the same transcript is
    carried over without a briefing or contradiction call unless ``fresh`` —
    that is what makes the sweep resumable. ``fresh`` also re-briefs.

    On ``KeyboardInterrupt`` the partial plan is saved (``complete: false``)
    before the interrupt propagates.
    """
    from mnemo.core.backfill.discover import recorded_cwd
    from mnemo.core.briefing import _load_jsonl_events, transcript_sha256
    from mnemo.core.extract.inbox.rendering import _extract_body
    from mnemo.core.mcp.recall_sessions import _transcripts_by_session
    from mnemo.core.transcript import user_turns

    vault_root = Path(vault_root)
    cutoff = _since_bound(since)
    if projects_root is None:
        projects_root = Path.home() / ".claude" / "projects"
    if briefer is None:
        from mnemo.core.config import load_config

        briefer = default_briefer(load_config(), fresh=fresh)
    out_path = plan_path(vault_root)
    scratch_briefings = scratch_root(vault_root) / "briefings"

    previous: dict = {}
    if not fresh and out_path.exists():
        try:
            previous = {s.session_id: s for s in load_plan(out_path).sessions}
        except Exception:  # a torn or foreign plan is a cache miss, not an error
            previous = {}

    result = BackfillPlan(
        run_id=datetime.now().strftime("%Y%m%dT%H%M%S"),
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        since=cutoff,
        project=project,
        fresh=bool(fresh),
    )

    on_disk = _transcripts_by_session(Path(projects_root))
    briefed = _briefed_sessions(vault_root)
    injected_by = _injected_by_session(vault_root)

    # (sort key, session id, transcript or None, day) — newest first.
    queue: list = []
    for sid, path in on_disk.items():
        mtime = _mtime(path)
        queue.append((mtime, sid, path, _day(mtime)))
    for sid, (_, md) in briefed.items():
        if sid not in on_disk:
            queue.append((_mtime(md), sid, None, _briefing_day(md)))
    queue.sort(key=lambda q: (q[0], q[1]), reverse=True)

    def emit(entry: SessionPlan) -> None:
        result.sessions.append(entry)
        save_plan(result, out_path)
        if progress is not None:
            progress(entry)

    try:
        for _, sid, path, day in queue:
            if cutoff is not None and day < cutoff:
                continue
            if path is None:
                proj = briefed[sid][0]
                if project is not None and proj != project:
                    continue
                emit(SessionPlan(session_id=sid, project=proj, status=GONE,
                                 detail="briefed, but the transcript is no longer on disk"))
                continue

            cwd = recorded_cwd([path])
            proj = _project_for(path, briefed.get(sid), cwd)
            if project is not None and proj != project:
                continue

            if corrections.is_job_scratch(cwd or ""):
                # Checked before the cache: a plan written before #348 holds
                # these sessions as found, links and all.
                emit(SessionPlan(session_id=sid, project=proj, status=SCRATCH,
                                 detail=f"ran in a background job's scratch dir ({cwd})"))
                continue

            sha = transcript_sha256(path) or ""
            cached = previous.get(sid)
            if cached is not None and cached.status in (FOUND, NONE_FOUND) \
                    and cached.transcript_sha256 == sha and cached.project == proj:
                emit(_without_shell_quotes(cached))
                continue

            entry = SessionPlan(session_id=sid, project=proj, status=NONE_FOUND,
                                transcript_sha256=sha, injected=list(injected_by.get(sid, [])))
            events = _load_jsonl_events(path)
            entry.ts = _session_start(events)
            turns = user_turns(events)
            reactions = turns[1:] if turns and corrections.is_dispatch_brief(turns[0]) else turns
            reactions = [t for t in reactions if not corrections.is_shell_turn(t)]
            if not reactions:
                # Nothing the user typed can carry a correction; a briefing call
                # here would be paid for a result verify() must reject.
                entry.detail = "no typed turns"
                emit(entry)
                continue

            try:
                written = briefer(path, proj, sid, scratch_briefings)
                result.briefing_calls += 1
                body = _extract_body(Path(written).read_text(encoding="utf-8"))
                proposed = corrections.parse_section(body)
            except Exception as exc:  # noqa: BLE001 — one bad session must not sink the sweep
                entry.status = FAILED
                entry.detail = f"{type(exc).__name__}: {exc}"
                emit(entry)
                continue
            entry.briefing = _vault_relative(Path(written), vault_root)

            kept, rejected = corrections.verify(proposed, turns)
            entry.rejected = len(rejected)
            if not kept:
                entry.detail = (f"{len(rejected)} proposed quote(s) not typed by the user"
                                if rejected else "")
                emit(entry)
                continue

            entry.status = FOUND
            for item in kept:
                entry.corrections.append(_link(
                    vault_root, item, project=proj, injected=entry.injected,
                    ranker=ranker, resolver=resolver, runner=runner,
                ))
                result.link_calls += 1
            emit(entry)
    except KeyboardInterrupt:
        save_plan(result, out_path)
        raise

    result.complete = True
    save_plan(result, out_path)
    return result


def _without_shell_quotes(entry: SessionPlan) -> SessionPlan:
    """A cached session as today's ``verify`` would have planned it.

    A plan saved before ``verify`` refused shell-mode turns (#360) can hold a
    ``<bash-input>`` quote; reusing it verbatim would put that quote back in
    the dry run and, on ``--apply``, in the ledger. Dropping it needs no model
    call — the verdict is mechanical — so the cache stays a cache.
    """
    kept = [c for c in entry.corrections if not corrections.is_shell_turn(c.quote)]
    if len(kept) == len(entry.corrections):
        return entry
    entry.rejected += len(entry.corrections) - len(kept)
    entry.corrections = kept
    if not entry.corrections:
        entry.status = NONE_FOUND
        entry.detail = f"{entry.rejected} proposed quote(s) not typed by the user"
    return entry


def _vault_relative(path: Path, vault_root: Path) -> str:
    try:
        return path.resolve().relative_to(vault_root.resolve()).as_posix()
    except (ValueError, OSError):
        return path.as_posix()


# --- apply -----------------------------------------------------------------------

def _existing_identities(vault_root: Path) -> set:
    from mnemo.core.log_utils import iter_rotated_rows

    seen = set()
    for row in iter_rotated_rows(ledger.ledger_path(vault_root)):
        sid, quote = row.get("session_id"), row.get("quote")
        if isinstance(sid, str) and isinstance(quote, str):
            seen.add((sid, corrections.normalize(quote)))
    return seen


def _telemetry() -> tuple:
    return ledger._telemetry()


def apply(vault_root: Path, plan: BackfillPlan) -> BackfillReport:
    """Write exactly what *plan* holds, as ``backfilled: true`` rows.

    No briefing and no contradiction pass run here. A correction the ledger
    already holds is counted as a duplicate and left alone, so a second apply
    writes nothing new.
    """
    vault_root = Path(vault_root)
    report = BackfillReport(run_id=plan.run_id)
    enabled, max_bytes = _telemetry()
    if not enabled:
        report.telemetry_off = True
        return report

    seen = _existing_identities(vault_root)
    for s in plan.sessions:
        if s.status != FOUND or not s.corrections:
            continue
        report.sessions += 1
        for c in s.corrections:
            if corrections.is_shell_turn(c.quote):
                continue  # a plan saved before #360; see _without_shell_quotes
            key = (s.session_id, corrections.normalize(c.quote))
            if key in seen:
                report.duplicates += 1
                continue
            rec = ledger.FrictionRecord(
                ts=s.ts,
                session_id=s.session_id,
                project=s.project,
                quote=c.quote,
                rule_text=c.rule,
                briefing=s.briefing,
                contradicts=list(c.contradicts),
                link_basis=c.link_basis,
                injected_in_session=list(s.injected),
                origin=ledger.ORIGIN_USER,
                backfilled=True,
            )
            rid = ledger.record(vault_root, rec)
            if rid is None:
                report.failed += 1
                continue
            seen.add(key)
            report.written.append(rid)
            _record_ranks(vault_root, rid, c, max_bytes)
    return report


def _record_ranks(vault_root: Path, rid: str, c: PlannedCorrection, max_bytes: int) -> None:
    """One sidecar row per confirmed link. Never raises — see the module docstring."""
    if not c.ranks:
        return
    try:
        from mnemo.core.log_utils import rotate_if_needed

        path = link_ranks_path(vault_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        rotate_if_needed(path, max_bytes)
        lines = "".join(
            json.dumps({"id": rid, "slug": slug, "rank": int(rank), "candidates": c.candidates},
                       ensure_ascii=False) + "\n"
            for slug, rank in c.ranks.items()
        )
        with open(path, "a", encoding="utf-8", newline="") as fh:
            fh.write(lines)
    except Exception as exc:
        ledger._log_failure(vault_root, exc)


# --- the report ------------------------------------------------------------------

RANK_BUCKETS = ((1, 5), (6, 10), (11, 20), (21, 40))


def _bucket(rank: int) -> str:
    for lo, hi in RANK_BUCKETS:
        if lo <= rank <= hi:
            return f"{lo}-{hi}"
    return f">{RANK_BUCKETS[-1][1]}"


def _live_rules(vault_root: Path) -> dict:
    """slug → frontmatter for every consumer-visible rule page."""
    from mnemo.core.filters import derive_rule_slug, is_consumer_visible, iter_shared_pages, parse_frontmatter

    out: dict = {}
    for md in iter_shared_pages(vault_root, include_inbox=False):
        try:
            fm = parse_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if not is_consumer_visible(md, fm, vault_root):
            continue
        out.setdefault(derive_rule_slug(fm, md.stem), fm)
    return out


def _retired_predicate() -> Optional[Callable]:
    """``is_retired`` once the retire piece has landed; ``None`` before."""
    try:
        from mnemo.core.friction.retire import is_retired
    except ImportError:
        return None
    return is_retired


def summarize(vault_root: Path, *, project: Optional[str] = None, since: Any = None) -> dict:
    """What ``mnemo friction`` prints. Read-only."""
    from mnemo.core.log_utils import iter_rotated_rows

    vault_root = Path(vault_root)
    records = list(ledger.iter_records(vault_root, project=project, since=_since_bound(since)))

    by_project: dict = {}
    by_origin: dict = {}
    by_basis = {b: 0 for b in ledger.LINK_BASES}
    contradicted: dict = {}
    for r in records:
        by_project[r.project or "?"] = by_project.get(r.project or "?", 0) + 1
        by_origin[r.origin] = by_origin.get(r.origin, 0) + 1
        by_basis[r.link_basis] = by_basis.get(r.link_basis, 0) + 1
        for slug in r.contradicts:
            contradicted.setdefault(slug, []).append(r.id)

    live = _live_rules(vault_root)
    is_retired = _retired_predicate()
    standing, retired, unknown = [], [], []
    for slug in sorted(contradicted):
        fm = live.get(slug)
        if fm is None:
            unknown.append(slug)
        elif is_retired is not None and _safe(is_retired, fm, vault_root):
            retired.append(slug)
        else:
            standing.append(slug)

    wanted = {(r.id, s) for r in records for s in r.contradicts}
    ranks: dict = {}
    seen_links = set()
    for row in iter_rotated_rows(link_ranks_path(vault_root)):
        key = (row.get("id"), row.get("slug"))
        rank = row.get("rank")
        if key in wanted and key not in seen_links and isinstance(rank, int):
            seen_links.add(key)
            ranks[_bucket(rank)] = ranks.get(_bucket(rank), 0) + 1
    order = [f"{lo}-{hi}" for lo, hi in RANK_BUCKETS] + [f">{RANK_BUCKETS[-1][1]}"]
    distribution = {b: ranks.get(b, 0) for b in order}

    links = len(wanted)
    return {
        "records": len(records),
        "backfilled": sum(1 for r in records if r.backfilled),
        "by_project": dict(sorted(by_project.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_origin": dict(sorted(by_origin.items())),
        "linked_records": sum(1 for r in records if r.contradicts),
        "links": links,
        "by_link_basis": by_basis,
        "corroborated": by_basis.get(ledger.LINK_EXTRACTOR_INJECTED, 0),
        "live_rules": len(live),
        "contradicted_live": standing,
        "contradicted_retired": retired if is_retired is not None else None,
        "contradicted_unknown": unknown,
        "rank_distribution": distribution,
        "rank_unknown": links - len(seen_links),
    }


def _safe(pred: Callable, fm: dict, vault_root: Path) -> bool:
    try:
        return bool(pred(fm, vault_root=vault_root))
    except Exception:
        return False


def _pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:.1f}%" if d else "-"


def format_summary(s: dict) -> str:
    lines = ["mnemo friction — what contradicted the vault", ""]
    if not s["records"]:
        lines.append("the ledger is empty. `mnemo friction --backfill` recovers corrections "
                     "from the transcripts still on disk.")
        return "\n".join(lines)
    lines.append(f"corrections   {s['records']:>5}    backfilled {s['backfilled']}")
    lines.append("  by project   " + ", ".join(f"{k} {v}" for k, v in s["by_project"].items()))
    lines.append("  by origin    " + ", ".join(f"{k} {v}" for k, v in s["by_origin"].items()))
    lines.append("")
    lines.append(f"linked        {s['linked_records']:>5}    ({s['links']} link(s))")
    for basis, n in s["by_link_basis"].items():
        lines.append(f"  {basis:<22}{n:>5}")
    lines.append(f"corroborated by an injection: {s['corroborated']} of {s['linked_records']} linked")
    lines.append("")
    standing = s["contradicted_live"]
    lines.append(f"live rules standing contradicted: {len(standing)} of {s['live_rules']} "
                 f"({_pct(len(standing), s['live_rules'])})")
    for slug in standing:
        lines.append(f"  {slug}")
    if s["contradicted_retired"]:
        lines.append(f"already retired: {len(s['contradicted_retired'])}")
    if s["contradicted_unknown"]:
        lines.append(f"named but not a live rule (flagged, never written anywhere): "
                     f"{len(s['contradicted_unknown'])}")
        for slug in s["contradicted_unknown"]:
            lines.append(f"  ? {slug}")
    lines.append("")
    lines.append("rank of confirmed links among the candidates shown")
    for bucket, n in s["rank_distribution"].items():
        lines.append(f"  {bucket:<8}{n:>5}")
    if s["rank_unknown"]:
        lines.append(f"  unknown {s['rank_unknown']:>5}")
    return "\n".join(lines)


def format_session(entry: SessionPlan) -> str:
    """One session's outcome, and each correction it carries."""
    label = LABELS.get(entry.status, entry.status)
    if entry.status == FOUND:
        label = f"{label} ({entry.found})"
    head = f"{entry.session_id[:8]}  {entry.project:<20} {label}"
    lines = [head + (f"  — {entry.detail}" if entry.detail else "")]
    for c in entry.corrections:
        quote = " ".join(c.quote.split())
        if len(quote) > 70:
            quote = quote[:70] + "…"
        lines.append(f'    "{quote}"')
        if c.contradicts:
            lines.append("      contradicts " + ", ".join(
                f"{slug} (#{c.ranks.get(slug, '?')})" for slug in c.contradicts))
        elif c.detail:
            lines.append(f"      {c.detail}")
    return "\n".join(lines)


def format_plan(p: BackfillPlan, *, sessions: bool = True) -> str:
    """The dry run. ``sessions=False`` prints the totals alone — for a caller
    that already streamed each session as it was planned."""
    counts = p.counts()
    lines = ["mnemo friction --backfill — corrections recovered from transcripts", ""]
    if sessions:
        lines.extend(format_session(s) for s in p.sessions)
        lines.append("")
    lines.append(f"sessions {len(p.sessions):>5}    briefings {p.briefing_calls}    "
                 f"contradiction passes {p.link_calls}"
                 + ("" if p.complete else "    (interrupted — rerun to continue)"))
    for status in STATUS_ORDER:
        lines.append(f"  {LABELS[status]:<20}{counts.get(status, 0):>5}")
    linked = sum(1 for s in p.sessions for c in s.corrections if c.contradicts)
    lines.append(f"corrections {p.corrections}, linked to a rule {linked}")
    lines.append("")
    lines.append(f"dry run — nothing written. --apply writes {p.corrections} correction(s) "
                 "to the ledger as backfilled, without another LLM call.")
    return "\n".join(lines)


__all__ = [
    "FAILED", "FOUND", "GONE", "LABELS", "LINK_RANKS_NAME", "NONE_FOUND", "PLAN_NAME",
    "SCRATCH", "SCRATCH_DIR", "STATUS_ORDER",
    "BackfillPlan", "BackfillReport", "PlannedCorrection", "SessionPlan",
    "apply", "format_plan", "format_session", "format_summary", "load_plan",
    "plan", "plan_path", "save_plan", "summarize",
]
