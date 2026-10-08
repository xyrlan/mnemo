"""The reflex judge's picks, kept by mnemo itself (#619).

#613 ranked a SessionStart block by how many earlier sessions the judge
picked each rule in, and found that the length of that history, not its
signal, is most of what it lacks. ``reflex-log.jsonl`` cannot hold it: it
rotates at 1 MiB and keeps one generation, a few days of rows. This ledger
keeps it, in two files under ``<vault>/.mnemo/``:

- :data:`LEDGER_NAME`, append-only, one row per judged prompt —
  ``{"ts", "session_id", "project", "picks"}``, ``picks`` the slugs the judge
  scored at or above ``injectAt``, possibly none. No prompt text, no rule
  text. Rows a :func:`backfill` wrote carry ``"backfilled": true``.
- :data:`SUMMARY_NAME`, what :func:`compact` rolled out of the ledger once it
  passed :data:`CAP_BYTES`: per project and rule, the number of distinct
  sessions whose first pick of it fell in each UTC day, or, past
  :data:`MONTHLY_AFTER_DAYS`, each month.

The hook's cost is :func:`record`: one append, no read, and nothing it raises
leaves it. :func:`compact` runs from SessionStart's detached work, never
from a hook a prompt waits on.

:func:`picks_before` answers "in how many distinct sessions before ``t`` was
each rule picked", never counting the asking session. Over raw rows the
answer is exact to the second. Over rolled-up history it is exact at a
bucket's end: a bucket counts once it has ended by ``t``, so a day's rolled
picks count from the next midnight UTC on. A session is rolled up whole or
not at all, so it is never counted in both files; and since its first pick
falls in a bucket that ends after the session started, its own rolled picks
never count as of its start.
"""
from __future__ import annotations

import json
import os
import time
from calendar import monthrange
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

LEDGER_NAME = "judge-picks.jsonl"
SUMMARY_NAME = "judge-picks-summary.json"
#: The ledger, renamed aside while :func:`compact` rolls it up; the hook
#: appends to a fresh :data:`LEDGER_NAME` meanwhile.
COMPACTING_SUFFIX = ".compacting"
_LOCK_NAME = "judge-picks.lock"
#: The ledger's size before :func:`compact` rolls its old sessions up.
CAP_BYTES = 1_048_576
#: How many days of sessions stay raw after a compaction.
KEEP_DAYS = 7
#: Rolled-up days older than this merge into their month.
MONTHLY_AFTER_DAYS = 90
_DAY = 86400.0


class AlreadyBackfilled(RuntimeError):
    """:func:`backfill` runs once per vault."""


def _dir(vault: Any) -> Path:
    return Path(vault) / ".mnemo"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(raw: Any) -> Optional[float]:
    if not isinstance(raw, str) or not raw:
        return None
    s = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# --- the hot path ------------------------------------------------------------------------

def record(vault: Any, *, session_id: str, project: str, picks: Sequence[str],
           ts: Optional[float] = None) -> None:
    """Append one judged prompt's row. One write; never reads, never raises."""
    try:
        row = {"ts": _iso(time.time() if ts is None else ts), "session_id": str(session_id),
               "project": str(project), "picks": [str(s) for s in picks]}
        path = _dir(vault) / LEDGER_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        _append_lines(path, [json.dumps(row)])
    except Exception:
        pass


def _append_lines(path: Path, lines: Sequence[str]) -> None:
    if not lines:
        return
    with path.open("a", encoding="utf-8") as fh:
        fh.write("".join(line + "\n" for line in lines))
        fh.flush()


# --- reading -----------------------------------------------------------------------------

def _read_rows(path: Path) -> Iterator[Dict[str, Any]]:
    """Every well-formed row of ``path``; torn lines and a missing file yield nothing."""
    from mnemo.core.log_utils import _iter_file_rows

    for row in _iter_file_rows(path):
        if _epoch(row.get("ts")) is not None and row.get("session_id") and \
                isinstance(row.get("picks"), list):
            yield row


def _token(path: Path) -> Optional[str]:
    """Names one held file, so a resumed compaction knows it already rolled it up."""
    try:
        st = path.stat()
    except OSError:
        return None
    return "%d:%d" % (st.st_size, st.st_mtime_ns)


def _read_summary(vault: Any) -> Dict[str, Any]:
    try:
        data = json.loads((_dir(vault) / SUMMARY_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("rules"), dict):
        data["rules"] = {}
    return data


def _last_seen(rows: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    last: Dict[str, float] = {}
    for row in rows:
        ts = _epoch(row["ts"])
        sid = row["session_id"]
        if ts is not None and (sid not in last or ts > last[sid]):
            last[sid] = ts
    return last


def _bucket_bounds(key: str) -> Optional[Tuple[float, float]]:
    """``YYYY-MM-DD`` or ``YYYY-MM`` as its (start, end) in epoch seconds, UTC."""
    try:
        if len(key) == 10:
            start = datetime.strptime(key, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return start.timestamp(), start.timestamp() + _DAY
        start = datetime.strptime(key, "%Y-%m").replace(tzinfo=timezone.utc)
        days = monthrange(start.year, start.month)[1]
        return start.timestamp(), start.timestamp() + days * _DAY
    except ValueError:
        return None


class Ledger:
    """The ledger and its summary, read once, for many :meth:`picks_before` calls."""

    def __init__(self, rows: Iterable[Dict[str, Any]], summary: Dict[str, Any]) -> None:
        #: ``(project, slug, session) -> first pick``, raw rows only.
        self.first: Dict[Tuple[str, str, str], float] = {}
        self.first_row: Optional[float] = None
        for row in rows:
            ts = _epoch(row["ts"])
            if ts is None:
                continue
            if self.first_row is None or ts < self.first_row:
                self.first_row = ts
            for slug in row["picks"]:
                key = (str(row.get("project") or ""), str(slug), str(row["session_id"]))
                if key not in self.first or ts < self.first[key]:
                    self.first[key] = ts
        self.summary = summary
        #: ``(project, slug, bucket start, bucket end, sessions)``.
        self.buckets: List[Tuple[str, str, float, float, int]] = []
        for project, rules in (summary.get("rules") or {}).items():
            for slug, by_key in (rules or {}).items():
                for key, n in (by_key or {}).items():
                    bounds = _bucket_bounds(key)
                    if bounds and isinstance(n, int) and n > 0:
                        self.buckets.append((project, slug, bounds[0], bounds[1], n))

    def picks_before(self, project: Optional[str], t: float,
                     session_id: Optional[str] = None) -> Dict[str, int]:
        """``{slug: distinct sessions}`` that picked it before ``t``, in
        ``project`` (every project when None), never ``session_id`` itself."""
        out: Dict[str, int] = {}
        # A session counts once per rule, even under two project names.
        seen = set()
        for (proj, slug, sid), ts in self.first.items():
            if ts < t and sid != session_id and (project is None or proj == project) \
                    and (slug, sid) not in seen:
                seen.add((slug, sid))
                out[slug] = out.get(slug, 0) + 1
        for proj, slug, _start, end, n in self.buckets:
            if end <= t and (project is None or proj == project):
                out[slug] = out.get(slug, 0) + n
        return out

    def since(self) -> Optional[float]:
        """The moment this ledger's history starts: a backfill's declared
        start, else its earliest row or rolled-up bucket."""
        found = [x for x in (self.summary.get("covers_since"), self.summary.get("first_row"),
                             self.first_row) if isinstance(x, (int, float))]
        found += [start for _, _, start, _, _ in self.buckets]
        return min(found) if found else None

    def window(self) -> Optional[Tuple[float, float]]:
        """The first and last pick it holds (a rolled-up bucket by its start)."""
        ts = list(self.first.values()) + [start for _, _, start, _, _ in self.buckets]
        return (min(ts), max(ts)) if ts else None


def load(vault: Any) -> Ledger:
    """The ledger as it stands, mid-compaction included."""
    d = _dir(vault)
    summary = _read_summary(vault)
    live = list(_read_rows(d / LEDGER_NAME))
    held_path = d / (LEDGER_NAME + COMPACTING_SUFFIX)
    held = list(_read_rows(held_path))
    if held and summary.get("applied") == _token(held_path):
        # Killed after the summary took the held file's old sessions: only
        # the sessions that compaction kept are still raw.
        cutoff = float(summary.get("applied_cutoff") or 0.0)
        last = _last_seen(held + live)
        held = [r for r in held if last[r["session_id"]] >= cutoff]
    return Ledger(held + live, summary)


def picks_before(vault: Any, project: Optional[str], t: float,
                 session_id: Optional[str] = None) -> Dict[str, int]:
    """``{slug: distinct sessions}`` the judge picked it in before ``t``,
    never counting ``session_id``. See :class:`Ledger` to ask many times."""
    return load(vault).picks_before(project, t, session_id)


# --- bounded -----------------------------------------------------------------------------

def _write_json(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _roll(summary: Dict[str, Any], rows: Sequence[Dict[str, Any]], now: float,
          monthly_after_days: float) -> None:
    first: Dict[Tuple[str, str, str], float] = {}
    for row in rows:
        ts = _epoch(row["ts"])
        for slug in row["picks"]:
            key = (str(row.get("project") or ""), str(slug), str(row["session_id"]))
            if ts is not None and (key not in first or ts < first[key]):
                first[key] = ts
    rules = summary["rules"]
    for (project, slug, _sid), ts in first.items():
        key = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        by_key = rules.setdefault(project, {}).setdefault(slug, {})
        by_key[key] = by_key.get(key, 0) + 1
    # A day older than the cutoff joins its month. Each session sits in the
    # day of its first pick, so the month's sum is still distinct sessions.
    cutoff = now - monthly_after_days * _DAY
    for by_project in rules.values():
        for slug, by_key in by_project.items():
            merged: Dict[str, int] = {}
            for key, n in by_key.items():
                bounds = _bucket_bounds(key)
                if len(key) == 10 and bounds and bounds[1] <= cutoff:
                    key = key[:7]
                merged[key] = merged.get(key, 0) + n
            by_project[slug] = dict(sorted(merged.items()))
    starts = [t for t in (_epoch(r["ts"]) for r in rows) if t is not None]
    if starts:
        old = summary.get("first_row")
        summary["first_row"] = min(starts) if not isinstance(old, (int, float)) else min(old, min(starts))


def compact(vault: Any, *, now: Optional[float] = None, cap: int = CAP_BYTES,
            keep_days: float = KEEP_DAYS, monthly_after_days: float = MONTHLY_AFTER_DAYS) -> str:
    """Roll the sessions whose last row is over ``keep_days`` old into the
    summary, once the ledger is past ``cap`` bytes. Off the hot path.

    The ledger is renamed aside first, so an append that races this lands in
    a fresh file and is never lost. The summary records which held file it
    took (``applied``), so a run killed halfway resumes without counting a
    session twice. Returns ``locked``, ``under_cap`` or ``compacted``.
    """
    from mnemo.core import locks

    d = _dir(vault)
    now = time.time() if now is None else now
    live = d / LEDGER_NAME
    held = d / (LEDGER_NAME + COMPACTING_SUFFIX)
    with locks.try_lock(d / _LOCK_NAME, stale_after=600.0) as got:
        if not got:
            return "locked"
        if not held.exists():
            try:
                if live.stat().st_size <= cap:
                    return "under_cap"
            except OSError:
                return "under_cap"
            os.replace(live, held)
        old = list(_read_rows(held))
        new = list(_read_rows(live))
        summary = _read_summary(vault)
        token = _token(held)
        if summary.get("applied") == token:
            cutoff = float(summary.get("applied_cutoff") or 0.0)
        else:
            cutoff = now - keep_days * _DAY
        last = _last_seen(old + new)
        kept = [r for r in old if last[r["session_id"]] >= cutoff]
        if summary.get("applied") != token:
            _roll(summary, [r for r in old if last[r["session_id"]] < cutoff], now, monthly_after_days)
            summary["applied"], summary["applied_cutoff"] = token, cutoff
            _write_json(d / SUMMARY_NAME, summary)
        # Kept rows go back after the ones appended meanwhile: the reader
        # orders by time, never by line.
        _append_lines(live, [json.dumps(r) for r in kept])
        held.unlink()
    return "compacted"


# --- backfill ----------------------------------------------------------------------------

def backfill(vault: Any, rows: Iterable[Dict[str, Any]], *, covers_since: float) -> int:
    """Append history from before the hook wrote this ledger, once.

    ``rows`` are ledger rows rebuilt from ``reflex-log.jsonl`` and the
    transcripts' reflex blocks. Each is marked ``backfilled``; a row at or
    after the hook's first own row is dropped (the ledger has it). The
    summary records ``covers_since``, the moment the backfilled history is
    complete from. Returns the rows written.
    """
    d = _dir(vault)
    summary = _read_summary(vault)
    ledger_rows = list(_read_rows(d / (LEDGER_NAME + COMPACTING_SUFFIX))) + list(_read_rows(d / LEDGER_NAME))
    if summary.get("covers_since") is not None or any(r.get("backfilled") for r in ledger_rows):
        raise AlreadyBackfilled("the judge-picks ledger was already backfilled")
    live = [_epoch(r["ts"]) for r in ledger_rows]
    live_since = min((t for t in live if t is not None), default=None)
    if summary.get("first_row") is not None:
        live_since = min(x for x in (live_since, summary["first_row"]) if x is not None)
    out = []
    for row in rows:
        ts = _epoch(row.get("ts"))
        if ts is None or not row.get("session_id") or not isinstance(row.get("picks"), list):
            continue
        if live_since is not None and ts >= live_since:
            continue
        out.append((ts, {"ts": _iso(ts), "session_id": str(row["session_id"]),
                         "project": str(row.get("project") or ""),
                         "picks": [str(s) for s in row["picks"]], "backfilled": True}))
    out.sort(key=lambda x: x[0])
    d.mkdir(parents=True, exist_ok=True)
    _append_lines(d / LEDGER_NAME, [json.dumps(r) for _, r in out])
    summary["covers_since"] = float(covers_since)
    _write_json(d / SUMMARY_NAME, summary)
    return len(out)
