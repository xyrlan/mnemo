"""The numbers the README is allowed to quote.

A README claim about how often mnemo fires, or how well it ranks, is only
honest if the reader can reproduce it on their own vault. So both figures come
out of files the tool already writes — ``.mnemo/reflex-log.jsonl`` (one row per
prompt, whether or not a rule was injected) and ``.mnemo/recall-report.json``
(the last ``mnemo recall`` run) — and ``mnemo status`` prints them in the same
shape the README uses.

Both readers are fail-safe by construction: a missing, truncated, or
hand-mangled file yields ``None``, never an exception and never a fabricated
zero. "No data" and "measured zero" are different answers, and a status line
that confuses them would be exactly the kind of claim this module exists to
prevent.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

# Rotation renames the live log at 1MB, so on a busy vault a 14-day window can
# straddle both files. Reading only the live one would under-report the total
# and quietly inflate the emit rate.
_LOG_NAMES = ("reflex-log.jsonl.1", "reflex-log.jsonl")
_REPORT_NAME = "recall-report.json"
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse a log ``ts`` into an aware UTC datetime, or None when unusable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, _TS_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:  # tolerate offsets/fractions from a hand-edited or older writer
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iter_rows(path: Path):
    """Yield dict rows from a JSONL file, skipping anything unreadable."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue  # a torn line from a killed append
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def is_reflex_opportunity(row: dict) -> bool:
    """Whether *row* is a prompt reflex actually had a chance to answer.

    A row whose ``silence_reason`` is ``all_exported`` was answered by the
    rules file Claude Code already loads, not by reflex — it was never a
    reflex opportunity, so any "how often does reflex fire" ratio must
    exclude it from both the numerator and the denominator. Every other row
    (an emission, or a silence for any other reason, including no reason at
    all) counts.

    This predicate defines the *user-facing* emit rate — ``reflex_emit_rate``
    below and ``autopilot/insights/digest.py`` both call this rather than
    re-deriving the check, so ``mnemo status`` and the weekly digest can't
    drift apart again. The reflex calibrator computes its own emit rate over
    a deliberately narrower "scored prompts" denominator (it also drops
    ``index_missing`` and ``below_min_tokens`` — see
    ``reflex_calibrator._DEAD_END_REASONS``) for calibration eligibility, a
    different question; it is not a bug for it to disagree with this
    function, and it should not be switched to call it.
    """
    return row.get("silence_reason") != "all_exported"


def reflex_emit_rate(
    vault_root: Path,
    *,
    days: int = 14,
    now: Optional[datetime] = None,
) -> Optional[Tuple[int, int]]:
    """``(emitted, total)`` prompts logged within the last ``days``.

    ``total`` counts every logged prompt — a silence is a row with an empty (or
    absent) ``emitted`` list — so the ratio is "how often a rule was injected",
    not "how often reflex ran at all". The one exception is a row whose
    ``silence_reason`` is ``all_exported``: that prompt was answered by the
    rules file Claude Code already loads, not by reflex, so it was never a
    reflex opportunity and is excluded from both sides of the ratio.

    Returns ``None`` when the log is missing or holds no row inside the window,
    which the caller renders as "no line" rather than "0%".
    """
    try:
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        cutoff = reference - timedelta(days=days)

        emitted = 0
        total = 0
        mnemo_dir = Path(vault_root) / ".mnemo"
        for name in _LOG_NAMES:
            for row in _iter_rows(mnemo_dir / name):
                ts = _parse_ts(row.get("ts"))
                if ts is None or ts < cutoff:
                    continue
                if not is_reflex_opportunity(row):
                    continue
                total += 1
                if row.get("emitted"):
                    emitted += 1
        if total == 0:
            return None
        return (emitted, total)
    except Exception:  # noqa: BLE001 — a status line is never worth a traceback
        return None


def recall_primacy(vault_root: Path) -> Optional[Tuple[float, int, str]]:
    """``(primacy_rate_at_5, cases, date)`` from the last ``mnemo recall`` run.

    ``date`` is the report's ``generated_at`` truncated to ``YYYY-MM-DD`` (empty
    when the report predates that field). ``None`` when the report is missing,
    corrupt, or measured nothing — a rate over zero cases is not a measurement.
    """
    try:
        path = Path(vault_root) / ".mnemo" / _REPORT_NAME
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        report = data.get("report")
        if not isinstance(report, dict):
            return None
        rate = report.get("primacy_rate_at_5")
        cases = report.get("cases")
        if not isinstance(rate, (int, float)) or isinstance(rate, bool):
            return None
        if not isinstance(cases, int) or isinstance(cases, bool) or cases <= 0:
            return None
        generated_at = data.get("generated_at")
        date = generated_at[:10] if isinstance(generated_at, str) else ""
        return (float(rate), cases, date)
    except Exception:  # noqa: BLE001 — a status line is never worth a traceback
        return None
