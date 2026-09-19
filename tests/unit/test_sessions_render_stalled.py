"""The STALLED bucket, and the hint it must never be shown beside (#393).

``claude rm`` deletes the worktree. On 2026-09-19 it was offered for six
children the account's limit had stopped, five of whose trees held
uncommitted work. Everything here is about keeping those two hints apart.
"""
from __future__ import annotations

import time

from mnemo.core.sessions.jobs import Session
from mnemo.core.sessions.render import render_queue
from mnemo.core.sessions.stalls import Kind, Stall

SOON = int(time.time()) + 3600
PAST = int(time.time()) - 3600


def _dead(short_id: str, cwd: str, needs: str) -> Session:
    """Blocked, and the roster proves the process gone — the ABANDONED shape."""
    return Session(short_id=short_id, state="blocked", tempo="blocked",
                   needs=needs, cwd=cwd, live=False,
                   updated_at="2026-09-19T03:51:57Z")


RATE_LIMITED = _dead("594436f2", "/r/mnemo-wt-380", "rate limited — wait and retry")
ASKING = _dead("aaaaaaaa", "/r/mnemo-wt-381", "which of the two shapes did you mean?")

FIVE_HOUR_FREE = Stall(kind=Kind.RATE_LIMIT, error="rate_limit",
                       resets_at=PAST, limit="five_hour")
FIVE_HOUR_WAITING = Stall(kind=Kind.RATE_LIMIT, error="rate_limit",
                          resets_at=SOON, limit="five_hour")


def test_a_rate_limited_child_is_not_offered_to_rm() -> None:
    out = render_queue([RATE_LIMITED], stalls={"594436f2": FIVE_HOUR_FREE})
    assert "STALLED (1)" in out
    assert "ABANDONED" not in out
    assert "claude rm" not in out
    assert "resume: mnemo resume" in out


def test_the_row_says_when_it_is_free_rather_than_how_old_it_is() -> None:
    free = render_queue([RATE_LIMITED], stalls={"594436f2": FIVE_HOUR_FREE})
    assert "free since" in free
    waiting = render_queue([RATE_LIMITED], stalls={"594436f2": FIVE_HOUR_WAITING})
    assert "frees at" in waiting
    assert "five-hour" in waiting


def test_the_footer_counts_only_the_ones_a_resume_would_wake() -> None:
    other = _dead("bbbbbbbb", "/r/mnemo-wt-382", "rate limited — wait and retry")
    out = render_queue([RATE_LIMITED, other],
                       stalls={"594436f2": FIVE_HOUR_FREE,
                               "bbbbbbbb": FIVE_HOUR_WAITING})
    assert "STALLED (2)" in out
    assert "(1 of 2 free now)" in out


def test_a_child_blocked_on_a_question_stays_abandoned() -> None:
    """Those need an answer, not a nudge — so the rm hint still belongs."""
    out = render_queue([ASKING], stalls={})
    assert "ABANDONED (1)" in out
    assert "STALLED" not in out
    assert "claude rm aaaaaaaa" in out


def test_the_two_buckets_coexist_and_each_keeps_its_own_hint() -> None:
    out = render_queue([RATE_LIMITED, ASKING], stalls={"594436f2": FIVE_HOUR_FREE})
    assert "STALLED (1)" in out and "ABANDONED (1)" in out
    assert "resume: mnemo resume" in out
    # The rm hint names the abandoned one, never the stalled one.
    assert "claude rm aaaaaaaa" in out
    assert "claude rm 594436f2" not in out


def test_a_human_stall_is_abandoned_however_it_was_classified() -> None:
    """A rate limit with no reset time is a credit cap; waiting fixes nothing."""
    credit_cap = Stall(kind=Kind.HUMAN, error="rate_limit")
    out = render_queue([RATE_LIMITED], stalls={"594436f2": credit_cap})
    assert "ABANDONED (1)" in out and "STALLED" not in out


def test_omitting_stalls_leaves_the_queue_exactly_as_it_was() -> None:
    """Every caller that predates this keeps working, byte for byte."""
    assert render_queue([RATE_LIMITED, ASKING]) == \
        render_queue([RATE_LIMITED, ASKING], stalls={})
    assert "ABANDONED (2)" in render_queue([RATE_LIMITED, ASKING])


def test_a_live_blocked_session_is_never_stalled() -> None:
    """It still has a process and is waiting on the maintainer, not a clock."""
    alive = Session(short_id="594436f2", state="blocked", tempo="blocked",
                    needs="rate limited — wait and retry", live=True,
                    updated_at="2026-09-19T03:51:57Z")
    out = render_queue([alive], stalls={"594436f2": FIVE_HOUR_FREE})
    assert "WAITING ON YOU (1)" in out
    assert "STALLED" not in out
