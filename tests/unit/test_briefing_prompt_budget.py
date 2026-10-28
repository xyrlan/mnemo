"""A long session's briefing prompt fits the model (#757).

A 2.3M-character transcript was sent whole, ~750K tokens against Haiku's
200K, and ``claude -p`` refused every one of those briefings.
"""
from __future__ import annotations

from mnemo.core import corrections
from mnemo.core.extract import prompts
from mnemo.core.extract.prompts.render import BRIEFING_PROMPT_MAX_CHARS


def _events(n: int) -> str:
    return "\n\n".join(f"[assistant] event {i} " + "w" * 200 for i in range(n))


def test_a_short_session_is_sent_whole():
    transcript = _events(10)
    prompt = prompts.build_briefing_prompt(transcript, user_turns=["fix it"])
    assert transcript in prompt
    assert "omitted" not in prompt


def test_an_oversized_transcript_keeps_its_start_and_end_within_the_ceiling():
    transcript = _events(12_000)  # ~2.5M characters, the size that failed
    assert len(transcript) > 5 * BRIEFING_PROMPT_MAX_CHARS
    prompt = prompts.build_briefing_prompt(transcript, user_turns=["fix it"])
    assert len(prompt) <= BRIEFING_PROMPT_MAX_CHARS
    assert "[assistant] event 0 " in prompt
    assert "[assistant] event 11999 " in prompt
    assert "characters of the session omitted here" in prompt
    assert prompt.endswith("=== END TRANSCRIPT ===\n")


def test_cuts_land_on_event_boundaries():
    prompt = prompts.build_briefing_prompt(_events(12_000), max_chars=50_000)
    body = prompt.split("=== TRANSCRIPT ===\n", 1)[1].rsplit("\n=== END TRANSCRIPT ===", 1)[0]
    head, tail = body.split("\n\n[… ", 1)
    tail = tail.split("…]\n\n", 1)[1]
    assert head.endswith("w" * 200)
    assert tail.startswith("[assistant] event ")


def test_too_many_turns_keep_the_newest_with_their_numbers():
    turns = [f"turn {i} " + "t" * 500 for i in range(1, 2001)]
    prompt = prompts.build_briefing_prompt("[user] hi", user_turns=turns, max_chars=100_000)
    assert len(prompt) <= 100_000
    assert "[2000] turn 2000 " in prompt
    assert "[1] turn 1 " not in prompt
    assert "turns are omitted" in prompt
    # The note is not a numbered turn, so it can never be quoted as one.
    note = next(line for line in prompt.splitlines() if "turns are omitted" in line)
    assert not note.startswith("[")
    assert corrections.locate("turns are omitted", turns) is None
