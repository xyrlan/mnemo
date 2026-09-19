"""Why a blocked session stopped, and whether a clock frees it (#393).

The two transcript fixtures are **real**, taken from ``~/.claude/projects`` on
2026-09-19 with every key and every number kept verbatim and only free text,
tool inputs and paths replaced by ``[redacted]`` — the exception being the API
error's own sentence, which is the evidence:

- ``stalled_rate_limit_594436f2.jsonl`` is the tail of child #380, one of the
  six the account's five-hour window stopped mid-turn, cut at the record it
  stopped on. It is the shape this whole feature exists for.
- ``stalled_credit_cap.jsonl`` is the *other* kind of ``rate_limit``: a
  per-model credit cap, ``quotaLimits`` absent. Four of the 18 rate limits on
  disk look like this, and none of them is freed by waiting, which is why the
  classifier asks for the epoch and not just the code.

The fixture is the point (``fixtures-that-lie-about-the-real-shape``): a
hand-written record with a ``resetsAt`` and nothing else would have passed
every assertion below while saying nothing about what Claude Code writes.
"""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core.sessions import stalls
from mnemo.core.sessions.jobs import Session
from mnemo.core.sessions.stalls import Kind

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "transcripts"

#: Child #380: `five_hour`, rejected, `resetsAt` 1789796400 — 2026-09-19
#: 05:40 UTC, which Claude Code rendered to the model as "resets 2:40am
#: (America/Sao_Paulo)".
REAL_RATE_LIMIT = FIXTURES / "stalled_rate_limit_594436f2.jsonl"
REAL_RESETS_AT = 1789796400
#: The moment the turn was cut, from the record's own timestamp.
BLOCKED_AT = 1789775517

REAL_CREDIT_CAP = FIXTURES / "stalled_credit_cap.jsonl"


def _blocked(path: Path | None = None, *, needs: str | None = None) -> Session:
    return Session(
        short_id="594436f2", state="blocked", tempo="blocked", needs=needs,
        link_scan_path=None if path is None else str(path),
        cwd="/Users/xyrlan/github/mnemo-wt-380",
        session_id="594436f2-a282-4a1c-b04a-08441296047e", live=False,
    )


# --- the real five-hour window ---------------------------------------------


def test_the_real_rate_limited_child_is_read_from_its_transcript() -> None:
    found = stalls.read_stall(_blocked(REAL_RATE_LIMIT))
    assert found is not None
    assert found.kind == Kind.RATE_LIMIT
    assert found.error == "rate_limit"
    assert found.resets_at == REAL_RESETS_AT
    assert found.limit == "five_hour"
    assert "session limit" in (found.text or "")


def test_it_is_not_free_before_the_reset_and_is_after() -> None:
    found = stalls.read_stall(_blocked(REAL_RATE_LIMIT))
    assert found.is_free(now=BLOCKED_AT) is False
    assert found.free_in(now=BLOCKED_AT) == REAL_RESETS_AT - BLOCKED_AT
    assert found.is_free(now=REAL_RESETS_AT) is True
    assert found.free_in(now=REAL_RESETS_AT + 60) == 0


def test_the_transcript_wins_over_the_needs_sentence() -> None:
    """Both sources agree on the kind; only one carries the reset time."""
    from_sentence = stalls.from_needs("rate limited — wait and retry")
    from_transcript = stalls.read_stall(
        _blocked(REAL_RATE_LIMIT, needs="rate limited — wait and retry"))
    assert from_sentence.kind == from_transcript.kind == Kind.RATE_LIMIT
    assert from_sentence.resets_at is None
    assert from_transcript.resets_at == REAL_RESETS_AT


def test_the_sentence_is_the_fallback_when_there_is_no_transcript() -> None:
    found = stalls.read_stall(_blocked(None, needs="rate limited — wait and retry"))
    assert found is not None and found.kind == Kind.RATE_LIMIT
    assert found.resets_at is None
    # No clock to wait for, so the honest answer to "can it be woken" is yes.
    assert found.is_free() is True


# --- the other rate limit, which no clock frees ----------------------------


def test_a_credit_cap_is_not_something_waiting_fixes() -> None:
    """A real ``rate_limit`` with no ``quotaLimits``: /usage-credits, not a clock."""
    record = json.loads(REAL_CREDIT_CAP.read_text(encoding="utf-8").splitlines()[0])
    assert record["error"] == "rate_limit", "fixture is not a rate limit any more"
    assert "quotaLimits" not in record or not record["quotaLimits"]

    found = stalls.from_record(record)
    assert found.kind == Kind.HUMAN
    assert found.clock_freed is False
    assert found.is_free() is False


# --- the rest of Claude Code's vocabulary ----------------------------------


def test_every_needs_sentence_maps_to_a_kind() -> None:
    """No sentence Claude Code writes falls through as "not a stall"."""
    for sentences in stalls.NEEDS_BY_ERROR.values():
        for sentence in sentences:
            found = stalls.from_needs(sentence)
            assert found is not None, sentence
            assert found.kind in (Kind.RATE_LIMIT, Kind.TRANSIENT, Kind.HUMAN)


def test_a_person_is_needed_for_everything_but_limits_and_retries() -> None:
    clock = {Kind.RATE_LIMIT, Kind.TRANSIENT}
    kinds = {
        error: stalls.from_needs(sentences[0]).kind
        for error, sentences in stalls.NEEDS_BY_ERROR.items()
    }
    assert {e for e, k in kinds.items() if k in clock} == {
        "rate_limit", "overloaded", "server_error"}
    # The ones a nudge would only replay: login, billing, verification, a
    # prompt that is too large, a request the API rejected.
    assert kinds["authentication_failed"] == Kind.HUMAN
    assert kinds["billing_error"] == Kind.HUMAN
    assert kinds["invalid_request"] == Kind.HUMAN


def test_a_retry_the_api_asked_for_has_no_clock() -> None:
    found = stalls.from_needs("API overloaded — wait and retry")
    assert found.kind == Kind.TRANSIENT
    assert found.resets_at is None
    assert found.is_free() is True
    assert found.free_in() is None


def test_an_unknown_sentence_is_not_invented_into_a_stall() -> None:
    assert stalls.from_needs("your turn to answer a question") is None
    assert stalls.from_needs("") is None
    assert stalls.from_needs(None) is None


# --- what is never asked ---------------------------------------------------


def test_a_working_session_is_never_read_for_a_stall() -> None:
    """Its transcript may hold an error it already retried past.

    Reading that as a stall would file a healthy child under STALLED and
    offer to wake a session that is awake — which ``--resume`` turns into a
    copy in its worktree.
    """
    working = Session(short_id="594436f2", state="working", tempo="active",
                      link_scan_path=str(REAL_RATE_LIMIT))
    assert stalls.read_stall(working) is None


def test_an_unreadable_transcript_costs_the_clock_not_the_row() -> None:
    found = stalls.read_stall(
        _blocked(FIXTURES / "does-not-exist.jsonl", needs="rate limited — wait and retry"))
    assert found is not None and found.kind == Kind.RATE_LIMIT
    assert found.resets_at is None


def test_stalls_for_skips_what_it_cannot_classify() -> None:
    blocked = _blocked(REAL_RATE_LIMIT)
    asking = Session(short_id="aaaaaaaa", state="blocked", tempo="blocked",
                     needs="which of the two shapes did you mean?", live=False)
    found = stalls.stalls_for([blocked, asking])
    assert set(found) == {"594436f2"}


def test_as_dict_carries_the_derived_answers() -> None:
    row = stalls.read_stall(_blocked(REAL_RATE_LIMIT)).as_dict()
    assert row["kind"] == Kind.RATE_LIMIT
    assert row["resets_at"] == REAL_RESETS_AT
    assert row["limit"] == "five_hour"
    assert row["clock_freed"] is True
    assert row["is_free"] is True


def test_free_at_is_a_wall_clock_or_nothing() -> None:
    assert stalls.free_at(None) == ""
    assert stalls.free_at(stalls.from_needs("rate limited — wait and retry")) == ""
    printed = stalls.free_at(stalls.read_stall(_blocked(REAL_RATE_LIMIT)))
    assert len(printed) == 5 and printed[2] == ":"
