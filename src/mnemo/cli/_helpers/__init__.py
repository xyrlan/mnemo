"""Small CLI-local helpers used by the status / doctor commands.

Absorbs the v0.9 PR-A flat module ``mnemo.cli_helpers`` (81L) into the
package layout introduced by PR H. Pure functions only — no argparse
wiring — kept independently importable so the helpers stay
unit-testable without booting the full CLI.

Zero behavior change vs. the previous ``mnemo.cli_helpers`` module.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _read_jsonl_tail(path: Path, max_lines: int) -> list[dict]:
    """Return the last *max_lines* decoded JSON objects from *path*.

    Unified implementation behind :func:`_read_denial_log_tail` and
    :func:`_read_enrichment_log_tail`. Blank lines and malformed JSON
    lines are skipped silently. Any read error (missing file, decode
    failure, permission) yields an empty list — callers treat the log
    as advisory data, never a hard dependency.
    """
    try:
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        entries: list[dict] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries
    except Exception:
        return []


def _read_denial_log_tail(vault: Path, max_lines: int = 1000) -> list[dict]:
    """Read last *max_lines* from denial-log.jsonl. Returns [] on any error."""
    return _read_jsonl_tail(vault / ".mnemo" / "denial-log.jsonl", max_lines)


def _read_enrichment_log_tail(vault: Path, max_lines: int = 1000) -> list[dict]:
    """Read last *max_lines* from enrichment-log.jsonl. Returns [] on any error."""
    return _read_jsonl_tail(vault / ".mnemo" / "enrichment-log.jsonl", max_lines)


def _count_today_denial_entries(entries: list[dict]) -> int:
    """Count entries whose timestamp starts with today's date (UTC)."""
    today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return sum(
        1 for e in entries
        if isinstance(e.get("timestamp"), str) and e["timestamp"].startswith(today_prefix)
    )


def _synthesize_path_for_glob(glob_pattern: str) -> str | None:
    """Produce a concrete file path that should match the glob, or None.

    Deterministic replacements:
      ``**/`` -> ``a/``   (match-zero-or-more-segments case)
      ``**``  -> ``a``    (trailing double-star)
      ``*``   -> ``sample`` (single segment)
    Returns None when the glob contains character classes or ``?`` — those
    cannot be safely synthesized without guessing which characters the author
    intended to match.
    """
    if "?" in glob_pattern or "[" in glob_pattern:
        return None
    out = glob_pattern.replace("**/", "a/").replace("**", "a").replace("*", "sample")
    return out or None
