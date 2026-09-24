"""The ``shared/_inbox/`` review queue: what waits there, and the acts that clear it.

Not :mod:`mnemo.core.extract.inbox`, which is the *writer* — the dispatch that
decides what stages. This is the reader: the queue a maintainer sees, the offer
that puts a page in front of them at session start, and ``promote`` / ``drop``,
the two decisions that take one out.

Why it exists (#380). ``shared/_inbox/`` fills by itself and drained only by a
hand ``mv``: 194 plain staged pages on the real vault on 2026-09-19, median age
5.3 days, oldest 16.2. A staged page is invisible to recall
(``filters.is_consumer_visible`` treats location as the authority on
draft-ness), so everything it holds carries nothing while it waits, and the only
surface that counted them was ``mnemo doctor`` (#375) — which you had to think
to run.

Three bounds keep the offer from becoming a nag. It rides on the session-start
prompt beside a briefing that already costs ~1783 tokens at 90.9% of starts, so
the budget is real:

* at most ``inbox.offerMax`` bullets per block (default 2);
* at most one block per project per ``inbox.offerIntervalHours`` (default 24);
* a page offered is not offered again for ``inbox.offerCooldownDays`` (default
  7), so the queue rotates rather than repeating its head at whoever is
  unlucky enough to start a session.

Every offer and every decision is appended to ``.mnemo/inbox-offers.jsonl``.
That ledger is not bookkeeping for its own sake: before it there was no record
that a page had ever been put in front of anyone, so "pages resolved per week"
and "how long from being shown to being judged" were not answerable questions.
:func:`stats` answers them from it.

The decision stays human, with two exceptions. A page the reference judge held
(#417 — it carries ``reference_gate: generic|narrative``) is archived by
:func:`expire_held` once it has sat ``inbox.heldExpiryDays`` untouched (#429):
44 offers over four days produced 0 decisions, so without an exit "held"
meant "kept forever, invisible". It rests on the judge's measurement. The
second is a page with the backfill origin (#496): the install review asks the
user once to keep or drop what the backfill learned, and a page they skipped
or never saw expires on the same clock, as the install-review spec
(``docs/superpowers/specs/2026-09-24-install-review-design.md``) decided.
Nothing else expires — a live-capture demotion or a multi-source page waits
for a human however old. The expiry is logged as ``expired``, never as a
decision, and :func:`restore` undoes it (or a drop).
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from mnemo.core.log_utils import iter_rotated_rows, rotate_if_needed

#: Append-only record of offers and decisions, under the vault's ``.mnemo/``.
LEDGER_REL = ".mnemo/inbox-offers.jsonl"
#: Same cap and shape as ``learned.jsonl``; ``log_utils`` rotates to ``.1``.
MAX_BYTES = 1_048_576

#: Ledger event names. ``offered`` is written by the session-start block,
#: ``expired`` by the extraction run, the rest by ``mnemo inbox``.
OFFERED = "offered"
PROMOTED = "promoted"
DROPPED = "dropped"
EXPIRED = "expired"
RESTORED = "restored"

#: Where a dropped page is archived before it is unlinked.
DROPPED_ARCHIVE_PREFIX = "dropped-"
#: Where an expired page is archived. Its own prefix, so an archive listing
#: says which pages a human threw away and which ran out of time.
EXPIRED_ARCHIVE_PREFIX = "expired-"
#: ``inbox.heldExpiryDays`` when config does not say (#429). The one window
#: both expiring kinds share — judge-held and undecided backfill pages (#496) —
#: so read it through :func:`held_expiry_days` or :func:`expires_at`, never
#: restate it.
HELD_EXPIRY_DAYS = 14

#: Why :func:`expire_held` archived a page, on :attr:`DecisionResult.why`.
WHY_HELD = "held"
WHY_BACKFILL = "backfill"

#: The ``reference_gate`` frontmatter values a staged page may carry: the
#: reference judge's category, written on a page it staged (#417) or on an
#: evidence-gate demotion it rated (#432). Anything else reads as unjudged.
GATE_VERDICTS = ("generic", "narrative", "technique", "system")
#: Verdicts the judge would keep live — the pages a decision is worth most on.
_GATE_KEEP = ("technique", "system")
#: Verdicts the judge would let expire.
_GATE_LET_GO = ("generic", "narrative")
#: How a verdict reads next to a page's age and reason.
_GATE_LABELS = {
    "generic": "generic",
    "narrative": "narrative",
    "technique": "technique",
    "system": "system knowledge",
}


@dataclass(frozen=True)
class StagedPage:
    """One plain page waiting in ``shared/_inbox/<type>/``.

    ``key`` is ``<type>/<slug>`` — the same spelling ``mnemo rewrites`` uses
    for a staged rewrite, so the two review surfaces name a page the same way.
    """

    path: Path
    key: str
    type: str
    slug: str
    name: str
    description: str
    projects: tuple[str, ...]
    reason: str
    mtime: float
    #: Staged on the reference judge's G/N verdict (#429) — the only pages
    #: :func:`expire_held` may archive.
    gate_held: bool = False
    #: The judge's ``reference_gate`` verdict — one of :data:`GATE_VERDICTS`,
    #: or ``""`` when the page was never judged (#433).
    gate_verdict: str = ""
    #: Carries the backfill origin stamp (#496), whatever else happened to it
    #: since — :func:`expire_held` archives it once nobody decided it.
    backfill: bool = False

    @property
    def expiry_why(self) -> str:
        """:data:`WHY_HELD`, :data:`WHY_BACKFILL`, or ``""`` for a page that never expires."""
        if self.gate_held:
            return WHY_HELD
        if self.backfill:
            return WHY_BACKFILL
        return ""

    @property
    def gate_label(self) -> str:
        """``judge: <verdict>`` for a judged page, ``""`` for an unjudged one."""
        label = _GATE_LABELS.get(self.gate_verdict, "")
        return f"judge: {label}" if label else ""

    def age_days(self, now: float | None = None) -> int:
        import time

        ref = time.time() if now is None else now
        return max(0, int((ref - self.mtime) // 86400))


def median(values: list) -> float | None:
    """The median of *values*, or None when there are none.

    Module-level because two surfaces quote it — the ``mnemo inbox`` listing
    and ``--stats`` — and a queue that reports two different medians of the
    same pages is a queue nobody trusts.
    """
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (float(ordered[mid - 1]) + float(ordered[mid])) / 2


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _reason_for(fm: dict) -> str:
    """Why this page is staged — which decides what a reviewer does with it.

    Same order and same predicates as the ``doctor`` line (#375), so the two
    surfaces cannot disagree about a page's bucket: a demoted page carries the
    backfill stamp too when it came from a transcript, and the demotion is what
    the evidence gate did to it last.
    """
    from mnemo.core.backfill.origin import is_backfill_frontmatter
    from mnemo.core.extract.demotion import is_demoted_frontmatter

    if is_demoted_frontmatter(fm):
        return "demotion"
    if is_backfill_frontmatter(fm):
        return "backfill"
    if len(fm.get("sources") or []) >= 2:
        return "multi-source"
    return "other"


def _gate_verdict(fm: dict) -> str:
    value = str(fm.get("reference_gate") or "").strip().lower()
    return value if value in GATE_VERDICTS else ""


def _offer_rank(page: StagedPage) -> int:
    """Which pages the offer spends its slots on first (#433).

    The judge's keeps (``technique``/``system``) first: they are invisible to
    recall until a human promotes them and nothing else will ever move them.
    Its let-gos (``generic``/``narrative``) last: they expire on their own
    after ``inbox.heldExpiryDays``, so a slot spent on one is a slot spent on
    a page the queue was going to shed anyway. Unjudged pages sit between —
    no verdict says which side they fall on.
    """
    if page.gate_verdict in _GATE_KEEP:
        return 0
    if page.gate_verdict in _GATE_LET_GO:
        return 2
    return 1


def staged_pages(vault_root: Path, *, project: str | None = None) -> list[StagedPage]:
    """Every plain staged page, oldest first. Never raises.

    Scope is :func:`filters.iter_staged_pages` — the ``_inbox/<type>/`` dirs
    every writer targets, not an ``rglob`` of ``_inbox``, which would count the
    archive copies an older ``mnemo rewrites`` left behind.

    ``.proposed.md`` siblings are excluded: they are rewrites of a live rule and
    ``mnemo rewrites`` already owns them. Oldest first because age is what this
    queue is judged on; promoting the head is what moves the median.

    ``project`` filters to pages attributable to it, by the same rule the
    activation index uses (``bots/<name>/`` in a source path, else a
    ``project``/``projects`` key). A page with no attributable project — 2 of
    194 on the real vault — is returned only when ``project`` is None.
    """
    from mnemo.core.backfill.origin import is_backfill_frontmatter
    from mnemo.core.extract.reference_gate import is_held_frontmatter
    from mnemo.core.filters import is_proposed_sibling, iter_staged_pages, parse_frontmatter
    from mnemo.core.rule_activation.index import projects_for_rule

    out: list[StagedPage] = []
    for path in iter_staged_pages(Path(vault_root)):
        if is_proposed_sibling(path):
            continue
        try:
            fm = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace")) or {}
        except OSError:
            continue
        page_type = path.parent.name
        slug = path.stem
        projects = tuple(projects_for_rule(fm.get("sources") or [], frontmatter=fm))
        if project is not None and project not in projects:
            continue
        out.append(StagedPage(
            path=path,
            key=f"{page_type}/{slug}",
            type=page_type,
            slug=slug,
            name=str(fm.get("name") or slug),
            description=str(fm.get("description") or ""),
            projects=projects,
            reason=_reason_for(fm),
            mtime=_mtime(path),
            gate_held=is_held_frontmatter(fm),
            gate_verdict=_gate_verdict(fm),
            backfill=is_backfill_frontmatter(fm),
        ))
    out.sort(key=lambda p: (p.mtime, p.key))
    return out


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------


def ledger_path(vault_root: Path) -> Path:
    return Path(vault_root) / LEDGER_REL


def read_ledger(vault_root: Path) -> list[dict]:
    """Every ledger row, oldest first, rotated file included. Never raises."""
    try:
        return [
            row for row in iter_rotated_rows(ledger_path(vault_root))
            if row.get("key") and row.get("event")
        ]
    except Exception:  # noqa: BLE001 — every caller is on a hook or CLI path
        return []


def record(
    vault_root: Path,
    *,
    event: str,
    key: str,
    project: str | None = None,
    session_id: str | None = None,
    via: str | None = None,
) -> None:
    """Append one row. Never raises — a ledger row is not worth a session.

    ``via`` is written only when given (:data:`VIA_REVIEW`), so every row
    written before it existed, and every one written without it, keeps its
    shape.

    ``newline=""``: no CRLF translation on Windows. The rotation cap is a byte
    budget, and a row costing one more byte per line there would rotate the
    same ledger at a different record on a different platform.
    """
    try:
        path = ledger_path(vault_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        rotate_if_needed(path, MAX_BYTES)
        row = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            "key": key,
            "project": project or "",
            "session_id": session_id or "",
        }
        if via:
            row["via"] = via
        with open(path, "a", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
    except Exception:  # noqa: BLE001 — runs inside the session-start hook
        return None


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def last_offered(vault_root: Path) -> dict[str, datetime]:
    """Newest ``offered`` timestamp per page key. Never raises."""
    out: dict[str, datetime] = {}
    for row in read_ledger(vault_root):
        if row.get("event") != OFFERED:
            continue
        ts = _parse_ts(row.get("ts"))
        if ts is None:
            continue
        key = str(row["key"])
        if key not in out or ts > out[key]:
            out[key] = ts
    return out


def last_block_at(vault_root: Path, project: str) -> datetime | None:
    """When this project was last shown a block, from its newest offer row."""
    newest: datetime | None = None
    for row in read_ledger(vault_root):
        if row.get("event") != OFFERED or str(row.get("project") or "") != project:
            continue
        ts = _parse_ts(row.get("ts"))
        if ts is not None and (newest is None or ts > newest):
            newest = ts
    return newest


# ---------------------------------------------------------------------------
# The offer
# ---------------------------------------------------------------------------


def offer_settings(cfg: dict) -> dict:
    """The three bounds, resolved from config with the documented defaults."""
    raw = (cfg or {}).get("inbox") or {}

    def _int(key: str, default: int) -> int:
        try:
            return int(raw.get(key, default))
        except (TypeError, ValueError):
            return default

    return {
        "enabled": bool(raw.get("offerOnSessionStart", True)),
        "max": max(0, _int("offerMax", 2)),
        "cooldown_days": max(0, _int("offerCooldownDays", 7)),
        "interval_hours": max(0, _int("offerIntervalHours", 24)),
    }


def pick_offers(
    vault_root: Path,
    project: str,
    *,
    cfg: dict,
    now: datetime | None = None,
) -> tuple[list[StagedPage], int]:
    """``(pages to offer, total staged for this project)``. Never raises.

    The bounds are checked cheapest first, and the order is load-bearing: this
    runs on every session start, while the block it feeds is emitted at most
    once a day per project. Reading the ledger is a few kilobytes; walking the
    queue means parsing the frontmatter of every staged page — 40 ms over the
    195 on the maintainer's vault — so a session the interval will silence must
    never pay for it. A silenced call therefore reports ``0`` waiting: nobody
    reads a total that comes with no pages, and counting it would cost exactly
    what this ordering saves.

    The pages offered are not the queue's head but the fresh pages ordered by
    :func:`_offer_rank` — the judge's keeps, then the unjudged, then the pages
    it would let expire — oldest first within each. By age alone, the
    130 evidence-gate demotions the judge rated on 2026-09-22 (G 69, N 3,
    S 41, T 17) would spend most slots on the 72 that expire regardless.
    """
    try:
        settings = offer_settings(cfg)
        if not settings["enabled"] or settings["max"] == 0:
            return [], 0
        ref = now or datetime.now()
        interval = settings["interval_hours"]
        if interval:
            previous = last_block_at(vault_root, project)
            if previous is not None and ref - previous < timedelta(hours=interval):
                return [], 0
        waiting = staged_pages(vault_root, project=project)
        if not waiting:
            return [], 0
        cooldown = timedelta(days=settings["cooldown_days"])
        offered = last_offered(vault_root)
        fresh = [
            page for page in waiting
            if settings["cooldown_days"] == 0
            or page.key not in offered
            or ref - offered[page.key] >= cooldown
        ]
        # Stable sort: oldest first within each group, as ``waiting`` is.
        fresh.sort(key=_offer_rank)
        return fresh[: settings["max"]], len(waiting)
    except Exception:  # noqa: BLE001 — runs inside the session-start hook
        return [], 0


# ---------------------------------------------------------------------------
# The two decisions
# ---------------------------------------------------------------------------


@dataclass
class DecisionResult:
    """What a :func:`promote` or :func:`drop` actually did."""

    ok: bool
    message: str
    moved_to: Path | None = None
    state_updated: bool = False
    #: Set by :func:`expire_held`: which rule expired the page.
    why: str = ""


def _state_path(vault_root: Path) -> Path:
    return Path(vault_root) / ".mnemo" / "extraction-state.json"


def _update_state_entry(vault_root: Path, key: str, *, status: str, page: Path | None) -> bool:
    """Set the extraction entry's ``status`` (and hash) to match what is on disk.

    The extractor's ledger is how it decides what to do with a slug next run.
    Left alone, a promoted page's entry still says ``inbox``: the next run finds
    the staging file gone and the sacred one present, flips the status itself
    and stages an ``.update-proposed.md`` for a source that has not changed
    (``branches/inbox_flow._handle_inbox_status``). Writing the status here is
    what makes ``promote`` different from the ``mv`` it replaces.

    A ``drop`` writes ``dismissed``, which ``_handle_dismissed`` honours: the
    page does not come back unless the user runs ``mnemo extract --force``.

    Returns False when there is no entry for the key — imported and
    hand-written pages have none, and inventing one would give the extractor a
    source hash no source ever produced. Never raises.
    """
    from mnemo.core.extract.inbox.io import content_hash

    path = _state_path(vault_root)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    entries = state.get("entries")
    if not isinstance(entries, dict):
        return False
    entry = entries.get(key)
    if not isinstance(entry, dict):
        return False
    entry["status"] = status
    if page is not None:
        try:
            entry["written_hash"] = content_hash(page)
        except OSError:
            pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(json.dumps(state, indent=2).encode("utf-8"))
        tmp.replace(path)
    except OSError:
        return False
    return True


def _rebuild_indexes(vault_root: Path) -> None:
    """Make a promoted page reachable now, not after the next session start.

    Best-effort, and each index on its own: the page is already in the sacred
    dir, so a failed rebuild costs it visibility until the next hook rebuild,
    never the promotion. Same shape as ``reclassify_apply``.
    """
    from mnemo.core import errors

    try:
        from mnemo.core import rule_activation
        rule_activation.write_index(vault_root, rule_activation.build_index(vault_root))
    except Exception as exc:  # noqa: BLE001
        errors.log_error(vault_root, "inbox.rule_activation_index", exc)
    try:
        from mnemo.core.reflex import index as reflex_index
        reflex_index.write_index(vault_root, reflex_index.build_index(vault_root))
    except Exception as exc:  # noqa: BLE001
        errors.log_error(vault_root, "inbox.reflex_index", exc)


def promote(
    vault_root: Path, page: StagedPage, *, project: str | None = None,
    via: str | None = None, rebuild: bool = True,
) -> DecisionResult:
    """Move a staged page into ``shared/<type>/`` and make it reachable.

    Refuses when a live page already holds the destination: that is two texts
    with one identity, and picking a winner here would silently drop one of
    them. ``mnemo rewrites`` is the surface for reconciling the two.

    The page's bytes are not touched. A demoted page keeps its
    ``demoted_from:`` line and a staged one keeps ``needs-review`` in ``tags``;
    both are history, and neither decides visibility — location does
    (``filters.is_consumer_visible``), which is exactly what this changes.

    ``rebuild=False`` leaves the indexes to the caller: :func:`decide_many`
    rebuilds once after a batch rather than once per page.
    """
    vault_root = Path(vault_root)
    dest = vault_root / "shared" / page.type / f"{page.slug}.md"
    if dest.exists():
        return DecisionResult(
            ok=False,
            message=(
                f"shared/{page.type}/{page.slug}.md already exists — two texts, one "
                "identity. Compare them and keep one; `mnemo rewrites` merges a "
                "staged rewrite of a live rule."
            ),
        )
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(page.path), str(dest))
    except OSError as exc:
        return DecisionResult(ok=False, message=f"could not move {page.key}: {exc}")

    state_updated = _update_state_entry(vault_root, page.key, status="promoted", page=dest)
    if rebuild:
        _rebuild_indexes(vault_root)
    record(vault_root, event=PROMOTED, key=page.key, project=project, via=via)
    return DecisionResult(
        ok=True,
        message=f"promoted {page.key} → shared/{page.type}/{page.slug}.md",
        moved_to=dest,
        state_updated=state_updated,
    )


def _archive_out(
    vault_root: Path, page: StagedPage, *, prefix: str, event: str,
    project: str | None, verb: str, via: str | None = None,
) -> DecisionResult:
    """Copy *page* under ``shared/_archive/<prefix><stamp>/``, then unlink it.

    Shared by :func:`drop` and :func:`expire_held`, which differ only in who
    decided: the entry goes ``dismissed`` either way, so a later extraction
    does not stage the same slug again from an unchanged source.
    """
    vault_root = Path(vault_root)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    arch = vault_root / "shared" / "_archive" / f"{prefix}{stamp}" / page.type
    try:
        arch.mkdir(parents=True, exist_ok=True)
        dest = arch / page.path.name
        dest.write_bytes(page.path.read_bytes())
        page.path.unlink()
    except OSError as exc:
        return DecisionResult(ok=False, message=f"could not archive {page.key}: {exc}")

    state_updated = _update_state_entry(vault_root, page.key, status="dismissed", page=None)
    record(vault_root, event=event, key=page.key, project=project, via=via)
    return DecisionResult(
        ok=True,
        message=f"{verb} {page.key}; archived to {dest.relative_to(vault_root).as_posix()}",
        moved_to=dest,
        state_updated=state_updated,
    )


def drop(
    vault_root: Path, page: StagedPage, *, project: str | None = None, via: str | None = None,
) -> DecisionResult:
    """Archive a staged page, then delete it from the queue.

    Archived first, always. Extraction re-derives a page from its source, so a
    drop is reversible only while that source still says the same thing — which
    makes "reject is not permanent" true of the decision and false of the text.
    ``mnemo rewrites --reject`` archives for the same reason, and
    :func:`restore` brings the archived copy back.
    """
    return _archive_out(vault_root, page, prefix=DROPPED_ARCHIVE_PREFIX,
                        event=DROPPED, project=project, verb="dropped", via=via)


def held_expiry_days(cfg: dict) -> int:
    """``inbox.heldExpiryDays``, or :data:`HELD_EXPIRY_DAYS`. ``0`` turns expiry off."""
    raw = ((cfg or {}).get("inbox") or {}).get("heldExpiryDays", HELD_EXPIRY_DAYS)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return HELD_EXPIRY_DAYS


def restored_keys(vault_root: Path) -> frozenset:
    """Keys a human pulled back out of the archive — they never expire again."""
    return frozenset(str(r["key"]) for r in read_ledger(vault_root) if r.get("event") == RESTORED)


def expires_at(
    page: StagedPage, cfg: dict | None = None, *, restored: frozenset = frozenset(),
) -> datetime | None:
    """When :func:`expire_held` will archive *page*, or None if it never will.

    The one place the rule is spelled for a reader (#496): the install-review
    listing's ``expires_at`` comes from here, so it cannot drift from the
    sweep. Staging time is the file's mtime, as :func:`expire_held` reads it.
    Pass :func:`restored_keys` as *restored* so a restored page reads None.
    The sweep runs at the end of an extraction, so a page is archived at the
    first extraction on or after this time, not at it.
    """
    days = held_expiry_days(cfg or {})
    if days <= 0 or not page.expiry_why or page.key in restored:
        return None
    return datetime.fromtimestamp(page.mtime) + timedelta(days=days)


def expire_held(
    vault_root: Path, *, days: int, now: datetime | None = None,
) -> list[DecisionResult]:
    """Archive every expiring page untouched for *days* or more. Never raises.

    "Untouched" is the file's mtime, the same age ``mnemo inbox`` lists: a
    page the extractor rewrites because its source changed starts its window
    again, since that is new evidence the judge has just looked at.

    Two kinds expire, on one window: the judge-held pages
    (:attr:`StagedPage.gate_held`, #429) and the backfill-origin pages nobody
    decided (:attr:`StagedPage.backfill`, #496). Never one a human restored —
    pulling a page back out of the archive is a decision to keep it waiting.
    Offered or not makes no difference: at ``inbox.offerMax`` = 2 offers a
    day per project, most held pages would never be shown, and an exit gated
    on being shown would not bound the queue. Each result's ``why`` says
    which rule archived it.
    """
    if days <= 0:
        return []
    try:
        ref = (now or datetime.now()).timestamp()
        restored = restored_keys(vault_root)
        out: list[DecisionResult] = []
        for page in staged_pages(vault_root):
            why = page.expiry_why
            if not why or page.key in restored or page.age_days(ref) < days:
                continue
            project = page.projects[0] if page.projects else None
            result = _archive_out(vault_root, page, prefix=EXPIRED_ARCHIVE_PREFIX,
                                  event=EXPIRED, project=project, verb="expired")
            result.why = why
            out.append(result)
        return out
    except Exception:  # noqa: BLE001 — runs at the end of every extraction
        return []


def _archived_copies(vault_root: Path, key: str) -> list[Path]:
    """Every archived copy of *key* a drop or an expiry left, newest first."""
    page_type, _, slug = key.partition("/")
    archive = Path(vault_root) / "shared" / "_archive"
    found = []
    for prefix in (DROPPED_ARCHIVE_PREFIX, EXPIRED_ARCHIVE_PREFIX):
        for path in archive.glob(f"{prefix}*/{page_type}/{slug}.md"):
            # The stamp, not the prefix, orders them: a page dropped after it
            # was restored from an expiry is newer than the expiry.
            found.append((path.parent.parent.name[len(prefix):], path))
    return [p for _, p in sorted(found, reverse=True)]


def restore(vault_root: Path, key: str, *, project: str | None = None) -> DecisionResult:
    """Put the newest archived copy of *key* back in ``shared/_inbox/``.

    Undoes an expiry or a drop. The state entry goes back to ``inbox`` so the
    extractor treats the page as staged again, and the ``restored`` row keeps
    :func:`expire_held` off it for good. Refuses when the slug is staged or
    live again already: two texts, one identity, same as :func:`promote`.
    """
    vault_root = Path(vault_root)
    page_type, _, slug = key.partition("/")
    if not page_type or not slug:
        return DecisionResult(ok=False, message=f"{key}: expected <type>/<slug>")
    copies = _archived_copies(vault_root, key)
    if not copies:
        return DecisionResult(ok=False, message=f"no dropped or expired copy of {key} in shared/_archive/")
    for where in (f"_inbox/{page_type}", page_type):
        if (vault_root / "shared" / where / f"{slug}.md").exists():
            return DecisionResult(
                ok=False,
                message=f"shared/{where}/{slug}.md already exists — {key} is back; nothing restored",
            )
    dest = vault_root / "shared" / "_inbox" / page_type / f"{slug}.md"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(copies[0]), str(dest))
    except OSError as exc:
        return DecisionResult(ok=False, message=f"could not restore {key}: {exc}")

    state_updated = _update_state_entry(vault_root, key, status="inbox", page=dest)
    record(vault_root, event=RESTORED, key=key, project=project)
    return DecisionResult(
        ok=True,
        message=f"restored {key} → shared/_inbox/{page_type}/{slug}.md",
        moved_to=dest,
        state_updated=state_updated,
    )


# ---------------------------------------------------------------------------
# The review: one listing, many decisions (#495)
# ---------------------------------------------------------------------------

#: ``via`` on a ledger row a batch or terminal review wrote (#495). A single
#: ``mnemo inbox --promote KEY`` writes no ``via``, so the install review's
#: drain can be told apart from the one the session-start offer drives.
VIA_REVIEW = "review"
#: How much of a page's body the JSON listing carries (#495).
EXCERPT_CHARS = 300


def match(pages: list[StagedPage], key: str) -> tuple[StagedPage | None, str]:
    """Find one page by ``<type>/<slug>`` or by a bare slug. ``(page, error)``.

    A bare slug is accepted only while it is unambiguous. Two types can hold
    the same slug — the cross-type duplicates #187 measured are exactly that —
    and guessing between them would act on the wrong page silently.
    """
    exact = [p for p in pages if p.key == key]
    if len(exact) == 1:
        return exact[0], ""
    by_slug = [p for p in pages if p.slug == key]
    if len(by_slug) == 1:
        return by_slug[0], ""
    if len(by_slug) > 1:
        names = ", ".join(sorted(p.key for p in by_slug))
        return None, f"{key} is staged under more than one type: {names}"
    return None, f"no staged page for {key}"


def _excerpt(path: Path) -> str:
    """The first :data:`EXCERPT_CHARS` of the body, secrets redacted.

    Redacted *before* it is cut: a cut can halve a token, and half a token is
    no longer a shape the patterns recognise but is still half a secret.
    """
    from mnemo.core.extract.scanner import parse_frontmatter
    from mnemo.core.redact import redact_secrets

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    _, body = parse_frontmatter(text)
    clean, _ = redact_secrets(body.strip())
    return clean[:EXCERPT_CHARS]


def page_record(page: StagedPage, *, cfg: dict, restored: frozenset) -> dict:
    """One ``pages[]`` row of the ``--json`` listing — the shape the desktop reads.

    ``expires_at`` is :func:`expires_at`'s answer, the rule the sweep itself
    runs (#496), so the date shown is the date it happens.
    """
    ends = expires_at(page, cfg, restored=restored)
    return {
        "key": page.key,
        "type": page.type,
        "name": page.name,
        "description": page.description,
        "excerpt": _excerpt(page.path),
        "staged_at": datetime.fromtimestamp(page.mtime).isoformat(timespec="seconds"),
        "expires_at": ends.isoformat(timespec="seconds") if ends else None,
    }


def listing(
    vault_root: Path, pages: list[StagedPage], *, project: str | None, origin: str, cfg: dict,
) -> dict:
    """The ``mnemo inbox --json`` document for *pages*, already scoped by the caller."""
    restored = restored_keys(vault_root)
    counts: dict[str, int] = {}
    for page in pages:
        counts[page.type] = counts.get(page.type, 0) + 1
    return {
        "project": project,
        "origin": origin,
        "pages": [page_record(p, cfg=cfg, restored=restored) for p in pages],
        "counts": counts,
    }


@dataclass
class Outcome:
    """One key of a batch: what was asked, which page it named, what happened."""

    key: str
    page_key: str | None
    result: DecisionResult


def decide_many(
    vault_root: Path, keys: list[str], *, action: str, via: str | None = VIA_REVIEW,
) -> list[Outcome]:
    """Promote or drop every key in *keys*, in order. Never raises.

    A failure on one key never stops the rest: each is matched against the
    queue as it stands after the ones before it, and anything a single
    decision raises is that key's failure. A key repeated in the batch is
    decided once. The indexes are rebuilt once at the end when anything was
    promoted — once per page would cost a 56-page review 56 rebuilds.
    """
    if action not in (PROMOTED, DROPPED):
        raise ValueError(f"action must be {PROMOTED!r} or {DROPPED!r}, got {action!r}")
    vault_root = Path(vault_root)
    pool = staged_pages(vault_root)
    out: list[Outcome] = []
    for key in dict.fromkeys(keys):
        page, err = match(pool, key)
        if page is None:
            out.append(Outcome(key, None, DecisionResult(ok=False, message=err)))
            continue
        project = page.projects[0] if page.projects else None
        try:
            if action == PROMOTED:
                result = promote(vault_root, page, project=project, via=via, rebuild=False)
            else:
                result = drop(vault_root, page, project=project, via=via)
        except Exception as exc:  # noqa: BLE001 — one key's failure is that key's
            result = DecisionResult(ok=False, message=f"could not decide {page.key}: {exc}")
        if result.ok:
            pool.remove(page)
        out.append(Outcome(key, page.key, result))
    if action == PROMOTED and any(o.result.ok for o in out):
        _rebuild_indexes(vault_root)
    return out


def batch_json(action: str, outcomes: list[Outcome]) -> dict:
    """``{"promoted"|"dropped": [...], "failed": [{"key", "error"}]}``.

    A decided key is reported as the page's full ``<type>/<slug>``, even when
    it was asked for by bare slug; a failed one as it was asked for.
    """
    return {
        action: [o.page_key for o in outcomes if o.result.ok],
        "failed": [{"key": o.key, "error": o.result.message} for o in outcomes if not o.result.ok],
    }



# ---------------------------------------------------------------------------
# The numbers
# ---------------------------------------------------------------------------


def stats(vault_root: Path, *, window_days: int = 7, now: datetime | None = None) -> dict:
    """What the queue is doing: depth, age, and drain over a window.

    ``resolved`` counts promotions and drops — both take a page out of the
    queue, and a reviewer who reads a page and throws it away has done the work
    this issue is about just as much as one who keeps it. ``expired`` is kept
    apart: nobody decided those (#429).

    ``median_decision_days`` is measured from a page's *first* offer to the
    decision that closed it, and is None until a page has been through both.
    That is the number that says whether the offer is what drained the queue,
    rather than someone sitting down with the directory again.
    """
    ref = now or datetime.now()
    since = ref - timedelta(days=window_days)
    waiting = staged_pages(vault_root)
    ages = sorted(p.age_days(ref.timestamp()) for p in waiting)

    first_offer: dict[str, datetime] = {}
    latencies: list[float] = []
    offered = promoted = dropped = expired = restored = 0
    for row in read_ledger(vault_root):
        ts = _parse_ts(row.get("ts"))
        event = row.get("event")
        key = str(row.get("key") or "")
        if ts is None or not key:
            continue
        if event == OFFERED and key not in first_offer:
            first_offer[key] = ts
        if event in (PROMOTED, DROPPED) and key in first_offer:
            latencies.append((ts - first_offer[key]).total_seconds() / 86400)
        if ts < since:
            continue
        if event == OFFERED:
            offered += 1
        elif event == PROMOTED:
            promoted += 1
        elif event == DROPPED:
            dropped += 1
        elif event == EXPIRED:
            expired += 1
        elif event == RESTORED:
            restored += 1

    return {
        "window_days": window_days,
        "staged": len(waiting),
        "median_age_days": median(ages),
        "oldest_age_days": ages[-1] if ages else None,
        "offered": offered,
        "promoted": promoted,
        "dropped": dropped,
        "resolved": promoted + dropped,
        "expired": expired,
        "restored": restored,
        "median_decision_days": median(latencies),
    }
