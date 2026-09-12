"""Format the session queue for a terminal.

Pure: takes :class:`Session` records, returns a string. No I/O, so the
ordering rules are testable without touching disk.

The ordering is the feature. Blocked first, oldest first — the maintainer
reads the first line and knows which session to attach to. Everything else
is context.
"""
from __future__ import annotations

from datetime import datetime, timezone

from mnemo.core.sessions.jobs import Session

EMPTY = "  nenhuma sessão em background"
NO_TIMESTAMP = "9999"  # sorts after every real ISO-8601 timestamp


def _age(updated_at: str | None, *, now: datetime | None = None) -> str:
    """Human age of the last update, or '' when unknown."""
    if not updated_at:
        return ""
    try:
        ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if ts.tzinfo is None:  # a writer that omitted the offset means UTC, as in core/numbers.py
        ts = ts.replace(tzinfo=timezone.utc)
    delta = (now or datetime.now(timezone.utc)) - ts
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "agora"
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h"


def _sort_key(s: Session) -> str:
    """Oldest update first; sessions without a timestamp sort last.

    ISO-8601 sorts lexically, so the year 9999 is simply later than any
    timestamp a real session can carry.
    """
    return s.updated_at or NO_TIMESTAMP


def _tokens(s: Session) -> str:
    if s.tokens is None:
        return ""
    return f"{s.tokens // 1000}k" if s.tokens >= 1000 else str(s.tokens)


def _prs(s: Session) -> str:
    ids = [f"#{c.get('id')}" for c in s.children if c.get("kind") == "pr" and c.get("id")]
    return ", ".join(ids)


def render_queue(sessions: list[Session]) -> str:
    """Render the whole queue, blocked first."""
    if not sessions:
        return EMPTY

    blocked = sorted((s for s in sessions if s.is_blocked), key=_sort_key)
    done = [s for s in sessions if s.is_done and not s.is_blocked]
    working = [s for s in sessions if not s.is_blocked and not s.is_done]

    lines: list[str] = []

    if blocked:
        lines.append(f"TE ESPERANDO ({len(blocked)})")
        for s in blocked:
            age = _age(s.updated_at)
            lines.append(f"  {s.short_id}  {s.label:<22} {age:>5}  {s.needs or s.detail or '—'}")
            if s.suggested_reply:
                lines.append(f"        ↳ sugerido: \"{s.suggested_reply}\"")
        lines.append("")

    if working:
        lines.append(f"TRABALHANDO ({len(working)})")
        for s in working:
            lines.append(f"  {s.short_id}  {s.label:<22} {s.detail or '—':<34}{_tokens(s):>6}")
        lines.append("")

    if done:
        lines.append(f"PRONTAS ({len(done)})")
        for s in done:
            lines.append(f"  {s.short_id}  {s.label:<22} {_prs(s) or s.detail or '—':<34}{_tokens(s):>6}")
        lines.append("")

    if blocked:
        lines.append(f"  attach: claude attach {blocked[0].short_id}")

    return "\n".join(lines).rstrip() + "\n"
