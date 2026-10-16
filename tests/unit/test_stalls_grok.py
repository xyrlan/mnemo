"""``read_stall`` recognises a Grok rate limit (grok-host wave 2, grok-sessions).

The records are the ones every rate-limited turn ended with on grok 1.0.60
(2026-10-16, free account): a ``retry_state`` of type ``exhausted`` carrying
the API's sentence, then ``turn_completed`` with ``stop_reason:
"rate_limit"``, in ``updates.jsonl`` beside the session's
``chat_history.jsonl``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from mnemo.core.sessions import rewake, stalls
from mnemo.core.sessions.jobs import Session

SID = "01a144df-4e51-7cb0-8c01-9d426cd43968"

#: When the limit hit: ``turn_completed``'s ``_meta.agentTimestampMs``.
STOPPED_MS = 1792156853029
DAY = 24 * 3600

EXHAUSTED = ("API error (status 429 Too Many Requests): subscription:free-usage-exhausted: "
             "You've used all the included free usage for model grok-4.7 for now. Usage "
             "resets over a rolling 24-hour window — tokens (actual/limit): "
             "612533/600000. Upgrade to a Grok subscription for higher limits")


def _update(kind: str, timestamp: int, ms: int = 0, **fields: Any) -> Dict[str, Any]:
    params: Dict[str, Any] = {"sessionId": SID, "update": dict(sessionUpdate=kind, **fields)}
    if ms:
        params["_meta"] = {"agentTimestampMs": ms}
    return {"timestamp": timestamp, "method": "_x.ai/session/update", "params": params}


def _rate_limited_turn(reason: str = EXHAUSTED) -> List[Dict[str, Any]]:
    return [
        _update("retry_state", 1792156849, type="retrying", attempt=1, max_retries=15,
                reason="API error (status 429 Too Many Requests): You are sending requests "
                       "too quickly.", error_type="rate_limited"),
        _update("retry_state", 1792156852, 1792156852014, type="exhausted", attempts=0,
                reason=reason, is_rate_limited=True),
        _update("turn_completed", 1792156853, STOPPED_MS, prompt_id="p2",
                stop_reason="rate_limit", elapsed_ms=4510),
    ]


def _end_turn() -> List[Dict[str, Any]]:
    return [_update("turn_completed", 1792150000, prompt_id="p1", stop_reason="end_turn")]


def _history(tmp_path: Path, records: List[Dict[str, Any]]) -> str:
    directory = tmp_path / "sessions" / "%2Frepo" / SID
    directory.mkdir(parents=True)
    (directory / "chat_history.jsonl").write_text("", encoding="utf-8")
    (directory / "updates.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return str(directory / "chat_history.jsonl")


def _grok(path: str, **kw: Any) -> Session:
    fields = dict(short_id="6cd43968", session_id=SID, state="blocked", tempo="blocked",
                  live=False, needs="rate limited — wait and retry", link_scan_path=path,
                  cwd="/repo", agent="grok")
    fields.update(kw)
    return Session(**fields)


def test_a_rate_limit_is_dated_by_the_window_the_api_names(tmp_path):
    path = _history(tmp_path, _end_turn() + _rate_limited_turn())

    stall = stalls.read_stall(_grok(path))

    assert stall is not None
    assert stall.kind == stalls.Kind.RATE_LIMIT
    assert stall.error == "rate_limit"
    assert stall.limit == "rolling_24h"
    assert stall.resets_at == STOPPED_MS // 1000 + DAY
    assert stall.text == EXHAUSTED
    assert stall.clock_freed
    assert not stall.is_free(now=STOPPED_MS / 1000 + 3600)
    assert stall.is_free(now=STOPPED_MS / 1000 + DAY)


def test_the_window_is_read_not_assumed(tmp_path):
    reason = EXHAUSTED.replace("rolling 24-hour window", "rolling 6-hour window")
    stall = stalls.grok_stall(_history(tmp_path, _rate_limited_turn(reason)))

    assert (stall.limit, stall.resets_at) == ("rolling_6h", STOPPED_MS // 1000 + 6 * 3600)


def test_a_limit_whose_sentence_names_no_window_has_no_clock(tmp_path):
    stall = stalls.grok_stall(_history(tmp_path, _rate_limited_turn("Rate limited")))

    assert stall.kind == stalls.Kind.RATE_LIMIT
    assert stall.resets_at is None and stall.limit is None
    assert stall.text == "Rate limited"


def test_a_turn_that_ended_after_the_limit_is_no_stall(tmp_path):
    """Woken and finished: the old limit is history."""
    path = _history(tmp_path, _rate_limited_turn() + _end_turn())

    assert stalls.grok_stall(path) is None
    assert stalls.grok_stop_reason(Path(path).parent) == "end_turn"


def test_retries_of_an_earlier_turn_are_not_this_stops(tmp_path):
    records = _rate_limited_turn()[:2] + _end_turn() + [
        _update("turn_completed", 1792156900, 1792156900000, prompt_id="p3",
                stop_reason="rate_limit")]
    stall = stalls.grok_stall(_history(tmp_path, records))

    assert stall.text is None and stall.resets_at is None


def test_a_working_grok_child_has_no_stall(tmp_path):
    path = _history(tmp_path, _rate_limited_turn())

    assert stalls.read_stall(_grok(path, state="working", tempo="active", live=True)) is None


def test_without_updates_the_needs_sentence_still_says_it(tmp_path):
    stall = stalls.read_stall(_grok(str(tmp_path / "gone" / "chat_history.jsonl")))

    assert stall.kind == stalls.Kind.RATE_LIMIT and stall.resets_at is None


def test_a_claude_transcript_is_still_read_the_claude_way(tmp_path):
    transcript = tmp_path / "abc.jsonl"
    transcript.write_text(json.dumps({
        "type": "assistant", "isApiErrorMessage": True, "error": "rate_limit",
        "quotaLimits": {"resetsAt": 1792170000, "rateLimitType": "five_hour"},
        "message": {"content": [{"type": "text", "text": "limit"}]}}) + "\n", encoding="utf-8")
    claude = Session(short_id="aaaa1111", state="blocked", tempo="blocked", live=False,
                     link_scan_path=str(transcript))

    stall = stalls.read_stall(claude)

    assert (stall.limit, stall.resets_at) == ("five_hour", 1792170000)


def test_the_rewake_watcher_takes_a_grok_child_once_its_window_passed(tmp_path):
    session = _grok(_history(tmp_path, _rate_limited_turn()))
    found = stalls.stalls_for([session])

    free, waiting = rewake.split([session], found, {}, now=STOPPED_MS / 1000 + 60)
    assert (free, [s.short_id for s, _ in waiting]) == ([], ["6cd43968"])

    free, waiting = rewake.split([session], found, {}, now=STOPPED_MS / 1000 + DAY + 1)
    assert [s.short_id for s, _ in free] == ["6cd43968"] and waiting == []


@pytest.mark.parametrize("name", ["chat_history.jsonl", "updates.jsonl"])
def test_either_file_of_the_session_names_it(tmp_path, name):
    path = Path(_history(tmp_path, _rate_limited_turn())).with_name(name)

    assert stalls.grok_stall(str(path)).resets_at == STOPPED_MS // 1000 + DAY


def test_grok_stall_of_nothing_is_none():
    assert stalls.grok_stall(None) is None
