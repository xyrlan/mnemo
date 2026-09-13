"""Format the session queue for a terminal.

Pure: takes :class:`Session` records, returns a string. No I/O, so the
ordering rules are testable without touching disk.

The ordering is the feature. Waiting first, **newest first** — the maintainer
reads the first line and knows which session to attach to. Everything else
is context.

Newest first, not oldest: a blocked session's ``tempo`` is frozen at the
moment its process stopped writing, so the stalest entry is the one most
likely to be a corpse (#196). Sorting oldest-first pinned zombies to the top
and aimed the attach hint at the worst of them. Sessions whose process is
provably gone move to their own bucket — re-bucketed, never hidden, because
a queue that silently drops entries cannot be trusted to be complete.
"""
from __future__ import annotations

from datetime import datetime, timezone

from mnemo.core.sessions.jobs import Session

EMPTY = "  nenhuma sessão em background"
NO_TIMESTAMP = "9999"  # sorts after every real ISO-8601 timestamp

# The detail/activity column. Named rather than repeated as a literal because
# `_activity` has to budget against the same number the f-strings pad to — the
# two drifting apart is what breaks the table.
DETAIL_WIDTH = 34
# Below this, a cut target says nothing ("Bash Rel…"), so the tool name and the
# signals take the whole column instead.
MIN_TARGET = 8


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


NO_TIMESTAMP_DESC = ""  # sorts before every real ISO-8601 timestamp


def _sort_key(s: Session) -> str:
    """Oldest update first; sessions without a timestamp sort last.

    ISO-8601 sorts lexically, so the year 9999 is simply later than any
    timestamp a real session can carry. Used for the abandoned bucket, where
    oldest-first is the right order: the stalest corpse is the one to clear.
    """
    return s.updated_at or NO_TIMESTAMP


def _freshest_first(s: Session) -> str:
    """Newest update first; sessions without a timestamp sort last.

    Descending on the timestamp means the empty string — the absent one —
    would otherwise sort *first* and hand the attach hint to a session we know
    nothing about. Reversing the sort is not enough; the sentinel has to flip
    with it.
    """
    return s.updated_at or NO_TIMESTAMP_DESC


def _tokens(s: Session) -> str:
    if s.tokens is None:
        return ""
    return f"{s.tokens // 1000}k" if s.tokens >= 1000 else str(s.tokens)


def _prs(s: Session) -> str:
    ids = [f"#{c.get('id')}" for c in s.children if c.get("kind") == "pr" and c.get("id")]
    return ", ".join(ids)


def _activity(act, budget: int = DETAIL_WIDTH) -> str:
    """One column of what a session is doing, or '' when nothing is known.

    ``(+N)`` is the movement signal and ``↻`` the loop signal: a count that
    rises with an unchanged target is a session going in circles, which reads
    identically to progress without the mark.

    Budgeted to *budget* columns by cutting the **target**, never the signals.
    Measured on 204 real actions from nine dispatch children: 39% of them
    exceed the column (p90 44, max 47), and `f"{x:<34}"` pads without
    truncating — so the unbudgeted string shoved the token column 10-15
    places right on two lines in five and the queue stopped reading as a
    table. Cutting the whole string instead would have eaten `(+26)` and
    `↻` first, which are the two things worth showing.
    """
    if act is None or not act.tool:
        return ""

    tail = ""
    if act.since:
        tail += f" (+{act.since})"
    if act.repeated:
        tail += " ↻"

    head = act.tool if not act.target else f"{act.tool} {act.target}"
    if len(head) + len(tail) <= budget:
        return head + tail

    # Not enough room for tool + target + signals. Give the target whatever is
    # left after the signals, and cut it on a word boundary.
    room = budget - len(tail) - len(act.tool) - 2  # space before, ellipsis after
    if not act.target or room < MIN_TARGET:
        # A tool name alone already fills the column: the signals still matter
        # more than the rest of the name.
        return act.tool[: budget - len(tail)] + tail
    cut = act.target[:room]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return f"{act.tool} {cut.rstrip()}…{tail}"


def render_queue(sessions: list[Session], activities=None) -> str:
    """Render the whole queue, blocked first.

    *activities* maps ``short_id`` to :class:`~mnemo.core.activity.Activity`.
    Omitted, the output is byte-identical to the queue that shipped in v1.4.0 —
    the column is additive, and every caller that predates it keeps working.

    Only the working bucket uses it. A blocked session's claim on the
    maintainer is ``needs``; burying that under a tool name would invert the
    ordering the whole queue exists to provide.
    """
    if not sessions:
        return EMPTY

    acts = activities or {}

    waiting = sorted((s for s in sessions if s.is_waiting), key=_freshest_first, reverse=True)
    abandoned = sorted((s for s in sessions if s.is_abandoned), key=_sort_key)
    done = [s for s in sessions if s.is_done and not s.is_blocked]
    working = [s for s in sessions if not s.is_blocked and not s.is_done]

    lines: list[str] = []

    if waiting:
        lines.append(f"TE ESPERANDO ({len(waiting)})")
        for s in waiting:
            age = _age(s.updated_at)
            lines.append(f"  {s.short_id}  {s.label:<22} {age:>5}  {s.needs or s.detail or '—'}")
            if s.suggested_reply:
                lines.append(f"        ↳ sugerido: \"{s.suggested_reply}\"")
        lines.append("")

    if working:
        lines.append(f"TRABALHANDO ({len(working)})")
        for s in working:
            detail = _activity(acts.get(s.short_id)) or s.detail or "—"
            lines.append(f"  {s.short_id}  {s.label:<22} {detail:<{DETAIL_WIDTH}}{_tokens(s):>6}")
        lines.append("")

    if done:
        lines.append(f"PRONTAS ({len(done)})")
        for s in done:
            lines.append(f"  {s.short_id}  {s.label:<22} {_prs(s) or s.detail or '—':<{DETAIL_WIDTH}}{_tokens(s):>6}")
        lines.append("")

    if abandoned:
        # Listed, not hidden. These asked for a human and their process died
        # before getting one; the user decides whether that still matters.
        lines.append(f"ABANDONADAS ({len(abandoned)})")
        for s in abandoned:
            age = _age(s.updated_at)
            lines.append(f"  {s.short_id}  {s.label:<22} {age:>5}  {s.needs or s.detail or '—'}")
        lines.append("")

    if waiting:
        lines.append(f"  attach: claude attach {waiting[0].short_id}")
    if abandoned:
        lines.append(f"  limpar: claude rm {abandoned[0].short_id}")

    return "\n".join(lines).rstrip() + "\n"
