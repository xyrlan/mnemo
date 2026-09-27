"""System prompt for the corrections-only pass (#517).

A session that edited no file gets no briefing — there is nothing to hand off
and the briefing would feed extraction a summary of a conversation — but the
user may still have corrected the assistant in it. This pass asks for the
``## Corrections`` section alone, under the briefing's own definition
(:data:`~mnemo.core.extract.prompts.templates.briefing.CORRECTIONS_DEFINITION`),
from the user's turns and the assistant text each one answered.

The precision paragraph is this pass's alone. Asked for nothing but
corrections, Haiku over-finds them. On the maintainer's ~60 no-edit human
sessions from 2026-08-31 to 09-27, the definition alone kept 57 verified
items; with the paragraph added it kept 43, and every item read as a real
correction in the first run was still there. Both runs remain mostly
approvals ("pode mergear"), requests and answers, by the maintainer's own
reading. So is the full briefing's section on the same weeks. `verify`
proves the user typed the words, not that the words correct anything (#517).
"""
from __future__ import annotations

from mnemo.core.extract.prompts.templates.briefing import CORRECTIONS_DEFINITION

CORRECTIONS_SYSTEM_PROMPT = (
    "You read a Claude Code session and list the user's corrections — and "
    "nothing else. The user message holds the session's numbered USER TURNS, "
    "each after the last thing the assistant said before it.\n\n"
    "Output MUST be markdown ONLY: the line `## Corrections` followed by one "
    "bullet per correction. A correction is "
    + CORRECTIONS_DEFINITION
    + "\n\n"
    "This pass is asked only for corrections, which makes it easy to find "
    "some where there are none. Hold every candidate to this test: the "
    "assistant line before the turn shows what the assistant did or "
    "proposed, and the user's turn rejects it, redirects it or constrains "
    "how it is done — or the user states a standing rule for future work "
    "('in this repo, always …'). These are never corrections, however they "
    "are phrased: telling the assistant to go ahead, merge, commit, open a "
    "PR or create an issue ('pode mergear', 'sim, commita e abre o PR', "
    "'cria a issue'); asking for new work; answering a question the "
    "assistant asked; asking a question; reporting a bug or a result. When "
    "in doubt, leave it out. Most sessions correct nothing, and then the "
    "right answer is empty: output nothing at all.\n\n"
    "Quote only from a line that starts with a USER TURN number. The "
    "assistant lines are there so you can tell what the user reacted to; "
    "never quote them. Do not wrap the output in code fences."
)
