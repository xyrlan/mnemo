"""What the rerank stage actually did, counted from the access log (#406).

The stage is silent by design: every gap — no key, a timeout, an HTTP error —
returns the BM25F order and says nothing to the agent, because a provider
being down must not break a tool call. The cost of that is a maintainer who
sets `recall.rerank.provider`, sees the list look exactly as before, and has
no way to tell "it ran and judged nothing worth reading" from "it has never
once had a key".

The server already writes the answer: a ``rerank`` object on each
``list_rules_by_topic`` row of ``.mnemo/mcp-access-log.jsonl``. This reads it
back. ``mnemo rerank``, ``mnemo status`` and nothing on the MCP path call it —
counting is for whoever is asking, never for the tool call itself.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from mnemo.core.log_utils import iter_rotated_rows

_LOG_FILENAME = "mcp-access-log.jsonl"

#: The statuses :func:`mnemo.core.mcp.rerank.apply` can report, in the order a
#: report reads best: what worked, then why it did not.
STATUSES = ("ok", "no_key", "error", "no_query", "small")

DEFAULT_DAYS = 14


def _cutoff(days: int) -> str:
    """The oldest timestamp still inside the window, as the log writes them.

    The log's timestamps are UTC ISO-8601 to the second with a ``Z``, which
    sorts lexicographically — so the window is a string comparison and a row
    whose timestamp is missing or malformed simply falls outside it.
    """
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def rows(vault_root: Path, *, days: int = DEFAULT_DAYS) -> List[Dict[str, Any]]:
    """Every ``rerank`` object logged inside the window, oldest first.

    The rotated ``.jsonl.1`` is read too: rotation happens at a size cap, so a
    14-day window on a busy vault straddles both files and reading only the
    live one would under-report exactly when there is most to report.
    """
    cutoff = _cutoff(days)
    found: List[Dict[str, Any]] = []
    for row in iter_rotated_rows(vault_root / ".mnemo" / _LOG_FILENAME):
        info = row.get("rerank")
        if not isinstance(info, dict):
            continue
        ts = row.get("timestamp")
        if not isinstance(ts, str) or ts < cutoff:
            continue
        found.append(info)
    return found


def summarize(vault_root: Path, *, days: int = DEFAULT_DAYS) -> Dict[str, Any]:
    """Counts over the window. Never raises; an absent log is a zero summary.

    ``marked_none`` counts the calls that ran, judged rules and marked none of
    them worth reading. That is a real answer — on the measured split 11 of 30
    queries had nothing in their topic about the task — so it is reported
    beside ``ok`` rather than inside a failure count.
    """
    summary: Dict[str, Any] = {
        "days": days,
        "calls": 0,
        "by_status": {},
        "judged": 0,
        "relevant": 0,
        "marked_none": 0,
        "providers": [],
    }
    providers = set()
    try:
        found = rows(vault_root, days=days)
    except Exception:  # noqa: BLE001 — a count is never worth a traceback
        return summary
    for info in found:
        summary["calls"] += 1
        status = str(info.get("status") or "unknown")
        summary["by_status"][status] = summary["by_status"].get(status, 0) + 1
        judged = info.get("judged")
        relevant = info.get("relevant")
        if isinstance(judged, int) and not isinstance(judged, bool):
            summary["judged"] += judged
        if isinstance(relevant, int) and not isinstance(relevant, bool):
            summary["relevant"] += relevant
            if status == "ok" and relevant == 0:
                summary["marked_none"] += 1
        provider = info.get("provider")
        if isinstance(provider, str) and provider:
            providers.add(provider)
    summary["providers"] = sorted(providers)
    return summary


def status_terms(by_status: Dict[str, int]) -> List[str]:
    """``<count> <status>`` terms, the known statuses first and in a fixed order.

    A status this version does not know about is still counted and still
    printed, after the ones it does: a row written by a newer mnemo must not
    vanish from an older one's report.
    """
    known = [s for s in STATUSES if by_status.get(s)]
    extra = sorted(s for s in by_status if s not in STATUSES)
    return ["%d %s" % (by_status[s], s) for s in known + extra]


def one_line(summary: Dict[str, Any]) -> Optional[str]:
    """The ``mnemo status`` line, or ``None`` when there is nothing to say.

    A majority of ``no_key`` is the failure this whole issue is about, so it
    is not left as one term among several — the line says outright that the
    stage is falling back on every call and what to run.
    """
    calls = int(summary.get("calls") or 0)
    if not calls:
        return None
    parts = ", ".join(status_terms(summary.get("by_status") or {}))
    line = "rerank: %d call%s in %dd, %s" % (
        calls, "" if calls == 1 else "s", int(summary.get("days") or DEFAULT_DAYS), parts)
    no_key = int((summary.get("by_status") or {}).get("no_key") or 0)
    if no_key * 2 > calls:
        line += (" — NO KEY on %d of %d calls: every one fell back to the local "
                 "order. Run `mnemo rerank --setup`." % (no_key, calls))
    return line
