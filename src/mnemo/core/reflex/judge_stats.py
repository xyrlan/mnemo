"""What the reflex judge actually did, counted from ``reflex-log.jsonl`` (#412).

The stage is silent by design: no key, a timeout or an HTTP error all fall
back to the decision the shipped gates made and tell the session nothing,
because a provider being down must not change what a prompt does. The cost of
that is a maintainer who turns it on, sees the same injections as before, and
cannot tell "it ran and turned them down" from "it has never once had a key".

The hook already writes the answer: a ``judge`` object on every row where the
stage ran. This reads it back for ``mnemo rerank``. Nothing on the prompt path
calls it — counting is for whoever is asking, never for the hook.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from mnemo.core.log_utils import iter_rotated_rows
from mnemo.core.reflex.judge import STATUSES

_LOG_FILENAME = "reflex-log.jsonl"

DEFAULT_DAYS = 14


def _cutoff(days: int) -> str:
    """The oldest timestamp still inside the window, as the log writes them.

    ``reflex-log`` stamps UTC ISO-8601 to the second with a ``Z``, which sorts
    lexicographically, so the window is a string comparison and a row with a
    missing or malformed stamp simply falls outside it.
    """
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def rows(vault_root: Path, *, days: int = DEFAULT_DAYS) -> List[Dict[str, Any]]:
    """Every ``judge`` object logged inside the window, oldest first.

    The rotated ``.jsonl.1`` is read too: the reflex log rotates at 1 MB, and
    on a busy vault a 14-day window straddles both files.
    """
    cutoff = _cutoff(days)
    found: List[Dict[str, Any]] = []
    for row in iter_rotated_rows(Path(vault_root) / ".mnemo" / _LOG_FILENAME):
        info = row.get("judge")
        if not isinstance(info, dict):
            continue
        ts = row.get("ts")
        if not isinstance(ts, str) or ts < cutoff:
            continue
        found.append(info)
    return found


def percentile(values: Sequence[float], fraction: float) -> Optional[float]:
    """Nearest-rank percentile over a sorted copy, or ``None`` when empty.

    Nearest-rank rather than interpolated: these are milliseconds off a
    handful of requests, and a p90 that is an observation actually made reads
    better than one between two of them.
    """
    ordered = sorted(values)
    if not ordered:
        return None
    index = int(round(fraction * (len(ordered) - 1)))
    return ordered[max(0, min(index, len(ordered) - 1))]


def summarize(vault_root: Path, *, days: int = DEFAULT_DAYS) -> Dict[str, Any]:
    """Counts over the window. Never raises; an absent log is a zero summary."""
    summary: Dict[str, Any] = {
        "days": days,
        "prompts": 0,
        "by_status": {},
        "asked": 0,
        "injected": 0,
        "median_ms": None,
        "p90_ms": None,
    }
    try:
        found = rows(vault_root, days=days)
    except Exception:  # noqa: BLE001 — a count is never worth a traceback
        return summary
    durations: List[float] = []
    for info in found:
        summary["prompts"] += 1
        status = str(info.get("status") or "unknown")
        summary["by_status"][status] = summary["by_status"].get(status, 0) + 1
        for key in ("asked", "injected"):
            value = info.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                summary[key] += value
        ms = info.get("ms")
        if isinstance(ms, (int, float)) and not isinstance(ms, bool):
            durations.append(float(ms))
    summary["median_ms"] = percentile(durations, 0.5)
    summary["p90_ms"] = percentile(durations, 0.9)
    return summary


def status_terms(by_status: Dict[str, int]) -> List[str]:
    """``<count> <status>`` terms, the known statuses first and in a fixed order.

    A status a newer mnemo writes is still counted and still printed, after
    the ones this version knows.
    """
    known = [s for s in STATUSES if by_status.get(s)]
    extra = sorted(s for s in by_status if s not in STATUSES)
    return ["%d %s" % (by_status[s], s) for s in known + extra]


def one_line(summary: Dict[str, Any]) -> Optional[str]:
    """One line for a report, or ``None`` when nothing was judged."""
    prompts = int(summary.get("prompts") or 0)
    if not prompts:
        return None
    line = "reflex judge: %d prompt%s in %dd, %s" % (
        prompts, "" if prompts == 1 else "s", int(summary.get("days") or DEFAULT_DAYS),
        ", ".join(status_terms(summary.get("by_status") or {})) or "no status recorded")
    no_key = int((summary.get("by_status") or {}).get("no_key") or 0)
    if no_key * 2 > prompts:
        line += (" — NO KEY on %d of %d: every one fell back to the lexical gate. "
                 "Run `mnemo rerank --setup`." % (no_key, prompts))
    return line
