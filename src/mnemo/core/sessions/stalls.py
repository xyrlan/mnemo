"""Why a background session stopped, and whether a clock will free it (#393).

A dispatched child that hits the account's five-hour limit stops mid-turn and
stays stopped. Its process dies, so :attr:`Session.is_abandoned` is True and
the queue filed it under ABANDONED beside ``remove: claude rm <id>`` — the one
hint that **deletes the worktree**. On 2026-09-19 that was offered for six
children (#380-#385), five of which held uncommitted work. None of them was
dead; all six finished with a PR once they were woken.

So the queue was not wrong about the process. It was wrong about the *claim*:
"blocked and the process is gone" does not mean "abandoned", it means "stopped
on something", and what that something is decides whether the answer is a
human or a clock.

**Two sources say what it was, and this module prefers the structured one.**

- ``state.json``'s ``needs`` is Claude Code's *rendered* sentence for the API
  error that stopped the turn — ``rate limited — wait and retry`` and the rest
  of :data:`NEEDS_BY_ERROR`, transcribed from the 2.1.278 binary. It is all
  there is when the transcript cannot be read, and it is a sentence, so it
  carries no reset time.
- The transcript carries the error itself. An API error is an ``assistant``
  record with ``isApiErrorMessage: true``, ``model: "<synthetic>"`` and zero
  usage (the same synthetic record ``activity.context`` already skips), whose
  ``error`` is Claude Code's own code and whose ``quotaLimits`` carries
  ``resetsAt`` — a unix epoch — and ``rateLimitType`` (``five_hour``,
  ``seven_day``). That epoch is the only machine-readable answer to "when is
  this free", and it is why the transcript is read first.

**The split is measured, not assumed.** Over the 79 API-error records in the
404 transcripts on this machine (2026-09-19): 23 ``authentication_failed``,
18 ``rate_limit``, 17 ``invalid_request``, 13 ``server_error``, 8 ``unknown``.
Of the 18 rate limits, **14 carry ``quotaLimits.resetsAt``** (``five_hour``
×13, ``seven_day`` ×1) and 4 do not — those 4 all read "You've reached your
Fable limit. Run /usage-credits to continue or switch models", which is a
per-model credit cap, not a window. So ``rate_limit`` alone does not mean a
clock frees it: :data:`Kind.RATE_LIMIT` requires the epoch, and a rate limit
without one is classified as needing a person, because the thing it needs is
a decision about credits.

**What this module refuses to decide.** Whether to spend the budget again.
It classifies and it says when; :mod:`mnemo.cli.commands.resume` is what acts.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from mnemo.core.sessions.jobs import Session

#: Claude Code's ``needs`` sentence per API-error code, transcribed from the
#: 2.1.278 binary's own switch (``TGr``) on 2026-09-19. The queue renders this
#: string; it is the fallback source when a transcript cannot be read, and the
#: only reason it is written down here is that Claude Code exposes the mapping
#: nowhere else. ``invalid_request`` has two answers depending on the message
#: text and both are listed; ``unknown`` and ``dlp_request_denied`` produce
#: ``state: failed`` with the bare sentence ``API error``.
#:
#: Checked against the installed binary by ``tests/live`` — a grep, no spawn —
#: so a rename upstream is reported rather than silently reclassifying every
#: stalled child as "needs a person".
NEEDS_BY_ERROR: Dict[Optional[str], Tuple[str, ...]] = {
    "authentication_failed": ("login required — run /login",),
    "oauth_org_not_allowed": ("org disabled OAuth — use API key or ask admin",),
    "account_on_hold": ("account on hold — see detail",),
    "cloud_credential_error": ("cloud credentials unavailable — check or refresh them",),
    "verification_required": ("organization verification required — see detail",),
    "billing_error": ("usage limit reached — check plan",),
    "rate_limit": ("rate limited — wait and retry",),
    "overloaded": ("API overloaded — wait and retry",),
    "server_error": ("API unavailable — retry",),
    "invalid_request": ("request too large — /compact or trim",
                        "invalid API request — see detail"),
    None: ("API error — see detail",),
    "unknown": ("API error",),
}


class Kind:
    """What it would take to get this session moving again.

    Three answers, and the difference between them is who acts:

    - :data:`RATE_LIMIT` — the account's window is spent and the reset time is
      known. Nobody has to answer anything; a clock does. This is the only
      kind that can say *when*.
    - :data:`TRANSIENT` — the API said retry and named no time
      (``overloaded``, ``server_error``). Waking it is the retry.
    - :data:`HUMAN` — a person has to do something outside the session:
      log in, fix billing, choose a model, trim a prompt. Waking it would
      re-run the same failing turn.
    """

    RATE_LIMIT = "rate_limit"
    TRANSIENT = "transient"
    HUMAN = "human"


#: Error codes a retry alone clears, with nothing to wait for but the attempt.
#: Both sentences Claude Code writes for them end in "retry".
_TRANSIENT_ERRORS = frozenset({"overloaded", "server_error"})


@dataclass(frozen=True)
class Stall:
    """Why one session stopped, as much of it as the evidence actually had.

    ``kind`` is the decision (:class:`Kind`). ``error`` is Claude Code's own
    code when the transcript gave one, and ``None`` when the classification
    came from the ``needs`` sentence alone — a reader can tell the two apart,
    which matters because only the first can carry ``resets_at``.
    """

    kind: str
    error: Optional[str] = None
    #: Unix epoch the account's window reopens, from ``quotaLimits.resetsAt``.
    #: ``None`` on every kind but :data:`Kind.RATE_LIMIT`.
    resets_at: Optional[int] = None
    #: ``five_hour`` / ``seven_day``, verbatim from ``quotaLimits``.
    limit: Optional[str] = None
    #: The sentence the model was handed, e.g. "You've hit your session limit
    #: · resets 2:40am (America/Sao_Paulo)". Rendered, never parsed: the
    #: epoch above is the machine-readable form of the same fact.
    text: Optional[str] = None

    @property
    def clock_freed(self) -> bool:
        """True when waiting, not a person, is what this session needs."""
        return self.kind in (Kind.RATE_LIMIT, Kind.TRANSIENT)

    def is_free(self, *, now: Optional[float] = None) -> bool:
        """True when nothing is left to wait for.

        A :data:`Kind.TRANSIENT` stall has no clock, so it is free as soon as
        it is seen — the wait *is* the retry. A :data:`Kind.RATE_LIMIT` one is
        free only past its ``resets_at``; a stall that is not clock-freed is
        never free, however old it is.
        """
        if not self.clock_freed:
            return False
        if self.resets_at is None:
            return True
        current = _now() if now is None else now
        return current >= self.resets_at

    def free_in(self, *, now: Optional[float] = None) -> Optional[int]:
        """Seconds until :meth:`is_free`, or ``None`` when there is no clock."""
        if self.resets_at is None:
            return None
        current = _now() if now is None else now
        return max(0, int(self.resets_at - current))

    def as_dict(self) -> Dict[str, Any]:
        """Plain data for ``mnemo sessions --json``, with the derived answers.

        The booleans ride along for the reason #222 gave the session's own:
        a consumer that re-implements ``is_free`` from ``resets_at`` gets the
        no-clock case wrong, and the no-clock case is a third of the
        population.
        """
        return {
            "kind": self.kind,
            "error": self.error,
            "resets_at": self.resets_at,
            "limit": self.limit,
            "text": self.text,
            "clock_freed": self.clock_freed,
            "is_free": self.is_free(),
            "free_in": self.free_in(),
        }


def _now() -> float:
    import time

    return time.time()


def _kind_for(error: Optional[str], resets_at: Optional[int]) -> str:
    """The decision, from the error code and whether a reset time came with it.

    ``rate_limit`` without an epoch is :data:`Kind.HUMAN` on purpose — see the
    four "Fable limit … run /usage-credits" records in the module docstring.
    Waking one of those replays the same rejection; what it needs is a choice
    about credits or a different model, which only a person makes.
    """
    if error in _TRANSIENT_ERRORS:
        return Kind.TRANSIENT
    if error == "rate_limit" and resets_at is not None:
        return Kind.RATE_LIMIT
    return Kind.HUMAN


def _int_or_none(value: Any) -> Optional[int]:
    """``value`` as an int, or ``None``. A bool is not an epoch."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _str_or_none(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def is_api_error(record: Dict[str, Any]) -> bool:
    """True when *record* is Claude Code's synthetic "the API refused" turn.

    Keyed on ``isApiErrorMessage`` rather than on ``model == "<synthetic>"``:
    the synthetic model marks every record the API did not produce, refusals
    included, while this flag marks the subset that stopped a turn. All 79 on
    this machine carry both.
    """
    return bool(record.get("isApiErrorMessage")) and record.get("type") == "assistant"


def from_record(record: Dict[str, Any]) -> Optional[Stall]:
    """One transcript record → a :class:`Stall`, or ``None`` if it is not one."""
    if not is_api_error(record):
        return None
    quota = record.get("quotaLimits")
    quota = quota if isinstance(quota, dict) else {}
    resets_at = _int_or_none(quota.get("resetsAt"))
    error = _str_or_none(record.get("error"))
    return Stall(
        kind=_kind_for(error, resets_at),
        error=error,
        resets_at=resets_at,
        limit=_str_or_none(quota.get("rateLimitType")),
        text=_message_text(record),
    )


def _message_text(record: Dict[str, Any]) -> Optional[str]:
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content or None
    if not isinstance(content, list):
        return None
    parts = [b.get("text", "") for b in content
             if isinstance(b, dict) and isinstance(b.get("text"), str)]
    joined = " ".join(p for p in parts if p).strip()
    return joined or None


def from_needs(needs: Optional[str]) -> Optional[Stall]:
    """The ``needs`` sentence → a :class:`Stall`, or ``None`` when it is not one.

    The fallback, for a session whose transcript is gone or unreadable. It can
    never carry a reset time, so a rate limit found this way is
    :data:`Kind.RATE_LIMIT` with ``resets_at=None`` — free as soon as it is
    seen, which is the honest reading of a sentence that says "wait and retry"
    and does not say how long.
    """
    if not needs:
        return None
    wanted = needs.strip()
    for error, sentences in NEEDS_BY_ERROR.items():
        if wanted not in sentences:
            continue
        if error in _TRANSIENT_ERRORS:
            return Stall(kind=Kind.TRANSIENT, error=error, text=wanted)
        if error == "rate_limit":
            # No epoch to check, so nothing distinguishes a spent window from
            # a spent credit balance here. Treated as the window, because the
            # sentence Claude Code chose for it is "wait and retry" — and a
            # wake that turns out to be the other one costs one failed turn.
            return Stall(kind=Kind.RATE_LIMIT, error=error, text=wanted)
        return Stall(kind=Kind.HUMAN, error=error, text=wanted)
    return None


def _tail_records(path: str, *, window: int) -> Tuple[Dict[str, Any], ...]:
    """The last *window* bytes of *path* as records. ``()`` on any failure.

    A dedicated read rather than :func:`activity.tail.read_tail`: that one
    owns a bookmark per session and is called on every queue tick, and
    borrowing its offsets here would consume events the activity column has
    not rendered yet.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return ()
    out = []
    try:
        with open(path, "rb") as fh:
            if size > window:
                fh.seek(size - window)
                fh.readline()  # drop the partial head line
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if isinstance(record, dict):
                    out.append(record)
    except OSError:
        return ()
    return tuple(out)


#: How much of the transcript's tail is read looking for the stall. A stalled
#: session stopped *at* its API error, so the record is the last one written;
#: this only has to be big enough to contain it plus whatever the turn before
#: it wrote. A quarter of :data:`activity.tail.WINDOW`, measured against the
#: six real children of 2026-09-19 — the error sat 1 to 3 records from the end
#: in all six.
TAIL_WINDOW = 65536


def read_stall(session: Session, *, window: int = TAIL_WINDOW) -> Optional[Stall]:
    """Why *session* stopped, or ``None`` when nothing says it stopped on anything.

    Transcript first, ``needs`` second, for the reason in the module
    docstring: only the transcript carries the reset epoch. Never raises — a
    transcript that cannot be read costs the reset time, not the row.

    Only asked of a session that is actually blocked. A working session's
    transcript may well contain an API error it already retried past, and
    reading that as a stall would file a healthy child under STALLED and
    offer to wake a session that is already awake — which ``--resume`` turns
    into a *copy* (``resume-under-own-id``).
    """
    if not session.is_blocked:
        return None
    path = session.link_scan_path
    if path:
        for record in reversed(_tail_records(path, window=window)):
            found = from_record(record)
            if found is not None:
                return found
    return from_needs(session.needs)


def stalls_for(sessions, *, window: int = TAIL_WINDOW) -> Dict[str, Stall]:
    """``short_id -> Stall`` for every blocked session that has one.

    Shaped like ``activity.activities_for``: a mapping the renderer and
    ``--json`` share, so neither re-derives it. A session that cannot be read
    is simply absent, never a partial record.
    """
    out: Dict[str, Stall] = {}
    for session in sessions or ():
        short_id = getattr(session, "short_id", None)
        if not short_id:
            continue
        try:
            found = read_stall(session, window=window)
        except Exception:
            continue
        if found is not None:
            out[short_id] = found
    return out


def free_at(stall: Optional[Stall]) -> str:
    """``02:40`` — local wall-clock of the reset, or '' when there is no clock.

    Local, not UTC: the maintainer reading the queue is deciding whether to
    wait, and Claude Code's own sentence ("resets 2:40am") is local too.
    """
    if stall is None or stall.resets_at is None:
        return ""
    try:
        moment = datetime.fromtimestamp(stall.resets_at, tz=timezone.utc).astimezone()
    except (OSError, OverflowError, ValueError):
        return ""
    return moment.strftime("%H:%M")
