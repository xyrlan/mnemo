"""Is this prompt a machine notice nobody typed? (#789)

Claude Code hands ``UserPromptSubmit`` every prompt it queues for the agent,
and some of them are the harness reporting something rather than a person
asking: a background task finished, another session sent a message, a child
mnemo dispatched is done. On the maintainer's vault, 2026-10-13 to 10-28,
45% of the prompts the reflex injected a rule into were notices
(``tools/measure_inject_band.py``, #764).

The hook's payload carries the prompt's text and nothing that says where it
came from — the transcript's ``origin.kind`` is written for the record, not
handed to the hook — so the frame is the signal. A prompt is a notice only
when the frame *is* the prompt: it opens with the envelope and, for the
envelopes that have one, ends with its closing tag. A typed prompt that
quotes a notice, or pastes one and asks about it underneath, reads as typed.

Not notices, on purpose: ``<mnemo-resume`` and ``<mnemo-pr-follow``. mnemo
writes those to wake a child with work to do (the account's limit lifted, the
PR went red), in the maintainer's voice; the child's next move is the work,
which is what a rule is for.
"""
from __future__ import annotations

from typing import Optional, Tuple

#: Envelopes with a closing tag: ``(opening, closing, kind)``. Claude Code's
#: own, as they reach the hook.
_ELEMENTS: Tuple[Tuple[str, str, str], ...] = (
    ("<task-notification>", "</task-notification>", "task-notification"),
    ("<cross-session-message", "</cross-session-message>", "cross-session-message"),
    ("<agent-message", "</agent-message>", "agent-message"),
)

#: mnemo's notice that a dispatched child finished. Its frame has no closing
#: tag, and it is mnemo's own text; spelled out rather than imported from
#: :mod:`mnemo.core.sessions.inbox`, which the hook should not pay for on
#: every prompt. Pinned to ``inbox.NOTICE_PREFIX`` by a test.
CHILD_FINISHED = "<mnemo-child-finished"

#: A raw write to a session's inbox socket, framed by Claude Code: this header
#: opens the turn and :data:`PEER_TRAILER` opens its last paragraph. The same
#: pair :mod:`mnemo.core.sessions.detector` strips; pinned together by a test.
PEER_HEADER = "Another Claude session sent a message:"
PEER_TRAILER = "\n\nThis came from another Claude session — "

#: Every kind :func:`kind` can answer, for a reader counting them.
KINDS = tuple(k for _o, _c, k in _ELEMENTS) + ("mnemo-child-finished", "peer-message")


def kind(prompt: Optional[str]) -> Optional[str]:
    """The notice's kind (one of :data:`KINDS`), or ``None`` for a prompt a
    person typed. Never raises."""
    if not isinstance(prompt, str):
        return None
    text = prompt.strip()
    for opening, closing, name in _ELEMENTS:
        if text.startswith(opening) and text.endswith(closing):
            # ``<agent-message`` must not claim ``<agent-messages>`` and the like.
            nxt = text[len(opening):len(opening) + 1]
            if opening.endswith(">") or nxt in (">", " ", "\n", "\t", "\r"):
                return name
    if text.startswith(CHILD_FINISHED):
        nxt = text[len(CHILD_FINISHED):len(CHILD_FINISHED) + 1]
        if nxt in (">", " ", "\n", "\t", "\r"):
            return "mnemo-child-finished"
    if text.startswith(PEER_HEADER) and PEER_TRAILER in text:
        return "peer-message"
    return None
