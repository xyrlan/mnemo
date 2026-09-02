"""core/transcript.user_turns — the user's own words, nothing the harness wrote."""
from __future__ import annotations

from mnemo.core import transcript


def _user(content):
    return {"type": "user", "message": {"role": "user", "content": content}}


def _assistant(text):
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": text}]}}


def test_user_turns_keeps_only_human_text_in_order():
    events = [
        _user("add a retry helper"),
        _assistant("done"),
        _user([{"type": "text", "text": "no — never retry on 4xx, only on 5xx"}]),
        _user([{"type": "tool_result", "content": "ok"}]),          # plumbing
        _user("<task-notification>build finished</task-notification>"),  # harness
        _user("<local-command-stdout>foo</local-command-stdout>"),        # slash cmd
        {"type": "user", "message": "not-a-dict"},                        # malformed
        "garbage",
    ]
    assert transcript.user_turns(events) == [
        "add a retry helper",
        "no — never retry on 4xx, only on 5xx",
    ]


def test_user_turns_empty_when_no_events():
    assert transcript.user_turns([]) == []


def test_recall_sessions_reuses_transcript_filter():
    from mnemo.core.mcp import recall_sessions
    assert recall_sessions._SYNTHETIC is transcript.SYNTHETIC_TURN
    assert recall_sessions._plain_text is transcript.plain_user_text


def test_user_turns_drops_blank_and_empty_content():
    assert transcript.user_turns([_user("   "), _user(""), _user([])]) == []
