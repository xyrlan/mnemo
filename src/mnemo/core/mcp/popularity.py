"""Recent-read popularity for ranking tiebreaks in list_rules_by_topic.

Topic listings sort primarily by ``source_count``. When many rules tie at the
same source_count (the common case in growing vaults — e.g. ``mnemo:automation``
recently held 48 rules all at source_count=1), the alphabetical fallback pushes
historically-relevant rules out of the top-N by accident.

This module derives a soft tiebreak from ``mcp-access-log.jsonl``: the count of
``read_mnemo_rule`` calls per slug within a recent window. Time-bounded by
default (30 days) so popularity is recoverable — a rule that was hot a quarter
ago no longer dominates today.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

_LOG_FILENAME = "mcp-access-log.jsonl"
_DEFAULT_WINDOW_DAYS = 30


def _parse_iso8601_z(ts: str) -> datetime | None:
    """Parse an ISO-8601 ``...Z`` timestamp; return ``None`` on failure."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def load_recent_read_counts(
    vault_root: Path,
    *,
    window_days: int = _DEFAULT_WINDOW_DAYS,
    now: datetime | None = None,
) -> Counter[str]:
    """Count ``read_mnemo_rule`` events per slug within the last *window_days*.

    Returns an empty Counter when the access log is missing or unreadable —
    callers should treat absent popularity as zero (i.e. no tiebreak signal).
    """
    log_path = vault_root / ".mnemo" / _LOG_FILENAME
    if not log_path.is_file():
        return Counter()
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=window_days)
    counts: Counter[str] = Counter()
    try:
        raw_text = log_path.read_text(encoding="utf-8")
    except OSError:
        return Counter()
    for raw in raw_text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if entry.get("tool") != "read_mnemo_rule":
            continue
        ts = _parse_iso8601_z(entry.get("timestamp", ""))
        if ts is None or ts < cutoff:
            continue
        slug = (entry.get("args") or {}).get("slug")
        if slug:
            counts[slug] += 1
    return counts
