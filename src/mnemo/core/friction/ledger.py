"""The friction ledger — one appended row per correction, and nothing else.

The vault's learning loop is open: a session produces rules, and nothing ever
tells a rule it was wrong. The ledger is the durable end of the closing half.
Every correction the extractor finds becomes a structured row in
``<vault>/.mnemo/friction-ledger.jsonl`` naming the rule it contradicts,
instead of a line of briefing prose that marks no rule and retires nothing.

This module is the on-disk contract and nothing more: records in, records
out. It runs no LLM, reads no rule, opens no page, and writes to no page.
Ranking candidates (``friction/candidates.py``), judging contradiction
(``friction/link.py``) and retiring a rule all live elsewhere and all consume
:class:`FrictionRecord`.

**The quote is not verified here.** ``core.corrections.verify`` is the
mechanical check that a quote really is a substring of something the user
typed, and the caller runs it *before* constructing a record. A caller that
skips it puts a fabricated quote in the ledger and this module will not catch
it. That boundary is deliberate — the ledger has no transcript to check
against — so do not add a verification assumption on top of it.

Follows ``briefing-log.jsonl`` (see ``core/mcp/access_log.py``) exactly where
the two overlap: the same telemetry switch, the same 1 MiB rotation, its own
file, and :func:`record` never raises. A failed write costs a ledger row,
never an extraction.

One consequence of that rotation is worth stating where a reader will meet
it, because the ledger is meant to accumulate history and a telemetry log is
not. ``rotate_if_needed`` keeps a single rotated generation: the second
rotation overwrites the first, and rows older than two files are gone. At the
measured ~8.75 corrections a day and ~440 bytes a row, 1 MiB is roughly nine
months of history and the backfill's projected 500–600 rows do not fill one
file — so nothing is lost today. If the report ever wants a window longer
than that, widening it is a change to the shared rotation, not to this
module, and it belongs in the piece that needs it.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from mnemo.core import corrections, errors
from mnemo.core.ci_corrections import ORIGIN_CI, ORIGIN_USER
from mnemo.core.log_utils import iter_rotated_rows, rotate_if_needed

#: The ledger's filename inside ``<vault>/.mnemo/``. Its own file rather than
#: a row kind in ``mcp-access-log.jsonl``: a correction is not an MCP call,
#: and sharing that file would push its 1 MiB rotation, shortening the window
#: ``mnemo recall`` reads its queried cases from.
LEDGER_NAME = "friction-ledger.jsonl"

#: The extractor named the contradicted rule; the reflex did not inject it.
LINK_EXTRACTOR = "extractor"
#: The extractor named it *and* the reflex had injected it into that same
#: session — the link is corroborated. Corroboration is recorded, never
#: required: only ~6.5% of prompts historically received an injection, so
#: demanding it would discard most real contradictions.
LINK_EXTRACTOR_INJECTED = "extractor+injected"
#: No rule was linked. A correction is never lost because the contradiction
#: pass failed, timed out, or returned nothing.
LINK_NONE = "none"

#: The only accepted ``link_basis`` values. A consumer branches on this, so a
#: typo must fail at construction rather than sit in the ledger unmatched by
#: every branch.
LINK_BASES = (LINK_EXTRACTOR, LINK_EXTRACTOR_INJECTED, LINK_NONE)

_ERROR_WHERE = "friction.ledger.record"
_DEFAULT_MAX_BYTES = 1_048_576

# 48 bits of identity. The design sketch showed six hex characters; at the
# 500–600 records the backfill alone is projected to recover that carries a
# ~1% chance of a collision, and a collision here silently merges two
# corrections into one. Twelve costs six characters and removes the question.
_ID_HASH_CHARS = 12
_ID_UNDATED = "00000000"
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class FrictionRecord:
    """One correction, as it is stored.

    Every field carries a default so the dataclass stays constructible by
    keyword on Python 3.8, which has no ``kw_only``. That is an ergonomic
    choice, not a statement that the fields are optional: a caller is
    expected to fill ``session_id``, ``project``, ``quote`` and ``rule_text``.

    ``id`` and ``ts`` are the two exceptions, and they are genuinely
    optional: :func:`record` stamps ``ts`` with the current UTC time and
    derives ``id`` via :func:`record_id` when they are left empty.

    ``link_basis`` is validated at construction — see :data:`LINK_BASES`.
    Nothing else is: the ledger is not the place that decides whether a
    correction is real, and a rejection here would be a rejection the
    extraction path has to handle.
    """

    #: ``f-<YYYYMMDD>-<hash>``; see :func:`record_id`.
    id: str = ""
    #: ``YYYY-MM-DDTHH:MM:SSZ``.
    ts: str = ""
    #: The session the correction was typed in.
    session_id: str = ""
    #: Project name, as the briefing directory spells it.
    project: str = ""
    #: The user's verbatim words. Verified by the caller, not here.
    quote: str = ""
    #: The imperative the briefing derived from the quote.
    rule_text: str = ""
    #: Vault-relative path of the briefing the correction was read from.
    briefing: str = ""
    #: Slugs of the rules this correction contradicts.
    contradicts: list[str] = field(default_factory=list)
    #: One of :data:`LINK_BASES`.
    link_basis: str = LINK_NONE
    #: Slugs the reflex injected into this session, whether or not they were
    #: contradicted. What turns ``extractor`` into ``extractor+injected``.
    injected_in_session: list[str] = field(default_factory=list)
    #: ``ci_corrections.ORIGIN_USER`` or ``ORIGIN_CI``. The CI channel (#272)
    #: writes to this same ledger.
    origin: str = ORIGIN_USER
    #: True when the row was recovered by the retroactive sweep rather than
    #: written as the session ended.
    backfilled: bool = False

    def __post_init__(self) -> None:
        if self.link_basis not in LINK_BASES:
            raise ValueError(
                "link_basis must be one of {}, got {!r}".format(
                    ", ".join(repr(b) for b in LINK_BASES), self.link_basis
                )
            )


def record_id(rec: FrictionRecord) -> str:
    """A stable, collision-resistant id for ``rec``.

    ``f-<YYYYMMDD>-<12 hex>``. The hash covers the record's *identity* —
    session plus normalised quote — and deliberately not its derived fields.
    The backfill re-reads the same transcripts and re-runs the contradiction
    pass over them; an LLM that names a different slug on the second pass
    must still produce the same id, or the rerun would write a second row for
    a correction already in the ledger. ``contradicts``, ``link_basis`` and
    ``injected_in_session`` are therefore outside the hash.

    The quote is normalised through :func:`corrections.normalize` for the
    same reason: a rerun that re-quotes the same words with curly quotes or
    different whitespace is the same correction.

    The date prefix is a reading affordance, not part of the identity — it
    makes a ledger scannable by eye and an id greppable by day. Two rows for
    one correction written on different days would carry different prefixes,
    which is why :func:`record` compares the identity and not the whole id.
    """
    material = "\x00".join((rec.session_id, corrections.normalize(rec.quote)))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:_ID_HASH_CHARS]
    return "f-{}-{}".format(_id_date(rec.ts), digest)


def record(vault_root: Path, rec: FrictionRecord) -> str | None:
    """Append ``rec`` to the ledger and return its id, or ``None``.

    ``None`` means *no row was appended*, and it covers every such case with
    one value: telemetry is switched off, the vault cannot be written to, or
    the row was refused as a duplicate. The id is a pure function of the
    record (:func:`record_id`), so a caller that needs it regardless can
    compute it without this call succeeding.

    **A duplicate ``(session_id, quote)`` is refused, not appended.** The
    backfill sweeps the same sessions again on every rerun — dry run, then
    ``--apply``, then a later ``--fresh`` — and an appending ledger would
    multiply one correction by the number of times it was swept, inflating
    exactly the count (*how much of what I hold has already been
    contradicted?*) that this ledger exists to produce. Refusing costs a read
    of the ledger per append, which at the measured ~8.75 corrections a day
    is nothing. The check is best-effort, not a lock: two processes appending
    at once can both miss, so a reader should tolerate a duplicate rather
    than assume the key is unique.

    Refusal is keyed on the quote, not on the whole record, so a rerun that
    resolves the link differently does not get a second row either. Rewriting
    an existing row is out of scope: the ledger is append-only, and a
    correction whose link improved is a correction the next pass over the
    ledger can re-judge.

    Never raises. A failed write logs one ``.errors.log`` row and returns
    ``None``; an extraction is never lost to a ledger failure.
    """
    try:
        enabled, max_bytes = _telemetry()
        if not enabled:
            return None

        root = Path(vault_root)
        resolved = _resolved(rec)
        log_path = ledger_path(root)
        if _already_recorded(log_path, resolved):
            return None

        log_path.parent.mkdir(parents=True, exist_ok=True)
        rotate_if_needed(log_path, max_bytes)
        # ensure_ascii=False: the corrections are largely Portuguese and the
        # ledger is meant to be read by eye, as ``learned.py`` writes its own
        # JSONL. Both forms parse identically.
        line = json.dumps(_to_row(resolved), ensure_ascii=False) + "\n"
        # newline="": no CRLF translation on Windows. The rotation cap is a
        # byte budget, and a row that costs one more byte per line there
        # would make the same ledger rotate at a different record on a
        # different platform. It also keeps the file byte-identical wherever
        # it was written, which a ledger meant to be greppable wants.
        with open(log_path, "a", encoding="utf-8", newline="") as fh:
            fh.write(line)
            fh.flush()
        return resolved.id
    except Exception as exc:  # never raises — a row is not worth an extraction
        _log_failure(vault_root, exc)
        return None


def iter_records(
    vault_root: Path,
    *,
    project: str | None = None,
    since: date | str | None = None,
) -> Iterator[FrictionRecord]:
    """Yield the ledger's records, oldest first.

    Reads the rotated sibling (``.jsonl.1``) before the live file, so a
    window that straddles a rotation is not silently truncated, and so the
    newest record is the last one yielded. There is exactly one such sibling
    — see the module docstring for what that bounds.

    ``project`` matches exactly. ``since`` is an inclusive lower bound on the
    record's date and takes a :class:`datetime.date` or an ``YYYY-MM-DD``
    string; a record whose ``ts`` is missing or unparseable is excluded by a
    ``since`` filter, because it cannot be shown to fall inside the window.

    A line that is not a record is skipped, never raised on: torn JSON from a
    killed append, a non-object row, and a row whose ``link_basis`` is not a
    value anything branches on. The ledger is a plain text file in the
    maintainer's vault and a hand edit must not crash every reader of it.
    A missing key is not corruption — it takes the field's default.
    """
    cutoff = _since_bound(since)
    for row in iter_rotated_rows(ledger_path(Path(vault_root))):
        rec = _from_row(row)
        if rec is None:
            continue
        if project is not None and rec.project != project:
            continue
        if cutoff is not None and rec.ts[:10] < cutoff:
            continue
        yield rec


def ledger_path(vault_root: Path) -> Path:
    """Where the ledger lives for ``vault_root``."""
    return Path(vault_root) / ".mnemo" / LEDGER_NAME


# --- internals -------------------------------------------------------------


def _telemetry() -> tuple[bool, int]:
    """The switch and size cap ``briefing-log.jsonl`` uses, read from it.

    Deliberately delegated rather than re-read: one switch means the ledger
    cannot drift from the log it is specified to follow, and a test that
    silences telemetry silences both.
    """
    try:
        from mnemo.core.mcp import access_log

        return access_log._load_telemetry_config()
    except Exception:
        return False, _DEFAULT_MAX_BYTES


def _utc_iso_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id_date(ts: str) -> str:
    day = (ts or "")[:10]
    return day.replace("-", "") if _ISO_DATE_RE.match(day) else _ID_UNDATED


def _resolved(rec: FrictionRecord) -> FrictionRecord:
    """``rec`` with ``ts`` and ``id`` filled in, in that order.

    ``ts`` first: the id's date prefix is read off it, so stamping the
    timestamp afterwards would date every fresh record ``00000000``. An id
    the caller set is kept — a record read back out of the ledger and handed
    to :func:`record` again keeps the id it was written under.
    """
    ts = rec.ts or _utc_iso_z()
    if ts != rec.ts:
        rec = replace(rec, ts=ts)
    return rec if rec.id else replace(rec, id=record_id(rec))


def _identity(session_id: str, quote: str) -> tuple[str, str]:
    return session_id, corrections.normalize(quote)


def _already_recorded(log_path: Path, rec: FrictionRecord) -> bool:
    """True when this ``(session_id, quote)`` is already in the ledger.

    Compares raw rows rather than parsed records on purpose: a row too
    malformed to parse still occupies its correction's slot, and re-appending
    beside it would leave the ledger holding the same correction twice.
    """
    wanted = _identity(rec.session_id, rec.quote)
    for row in iter_rotated_rows(log_path):
        if _identity(_text(row.get("session_id")), _text(row.get("quote"))) == wanted:
            return True
    return False


def _to_row(rec: FrictionRecord) -> dict[str, Any]:
    return {
        "id": rec.id,
        "ts": rec.ts,
        "session_id": rec.session_id,
        "project": rec.project,
        "quote": rec.quote,
        "rule_text": rec.rule_text,
        "briefing": rec.briefing,
        "contradicts": list(rec.contradicts),
        "link_basis": rec.link_basis,
        "injected_in_session": list(rec.injected_in_session),
        "origin": rec.origin,
        "backfilled": bool(rec.backfilled),
    }


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _slugs(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _from_row(row: dict[str, Any]) -> FrictionRecord | None:
    """A record from one ledger row, or ``None`` if the row is not one."""
    try:
        return FrictionRecord(
            id=_text(row.get("id")),
            ts=_text(row.get("ts")),
            session_id=_text(row.get("session_id")),
            project=_text(row.get("project")),
            quote=_text(row.get("quote")),
            rule_text=_text(row.get("rule_text")),
            briefing=_text(row.get("briefing")),
            contradicts=_slugs(row.get("contradicts")),
            # Absent means "the writer had nothing to say", which is LINK_NONE.
            # Present and unrecognised means corruption, and FrictionRecord
            # raises on it — the row is skipped by the caller.
            link_basis=_text(row.get("link_basis")) or LINK_NONE,
            injected_in_session=_slugs(row.get("injected_in_session")),
            origin=_text(row.get("origin")) or ORIGIN_USER,
            backfilled=bool(row.get("backfilled", False)),
        )
    except Exception:
        return None


def _since_bound(since: date | str | None) -> str | None:
    """``since`` as the ``YYYY-MM-DD`` string a ``ts`` prefix compares against.

    ISO dates sort lexicographically, so the comparison needs no parsing of
    the stored timestamps — which is what lets a malformed ``ts`` fall out of
    the window instead of raising on the way in.
    """
    if since is None:
        return None
    if isinstance(since, date):  # datetime is a date
        return since.strftime("%Y-%m-%d")
    text = str(since).strip()[:10]
    if not _ISO_DATE_RE.match(text):
        raise ValueError("since must be a date or 'YYYY-MM-DD', got {!r}".format(since))
    return text


def _log_failure(vault_root: Path, exc: BaseException) -> None:
    try:
        errors.log_error(Path(vault_root), _ERROR_WHERE, exc)
    except Exception:
        pass


__all__ = [
    "LEDGER_NAME",
    "LINK_BASES",
    "LINK_EXTRACTOR",
    "LINK_EXTRACTOR_INJECTED",
    "LINK_NONE",
    "ORIGIN_CI",
    "ORIGIN_USER",
    "FrictionRecord",
    "iter_records",
    "ledger_path",
    "record",
    "record_id",
]
