"""How much text a hook may hand Claude Code before it stops reading it inline.

Shared by the SessionStart envelope (#533) and the UserPromptSubmit reflex
block (#542): both are a hook's ``additionalContext``, and both are held under
the one cap below.
"""
from __future__ import annotations

#: The most a hook's ``additionalContext`` may weigh, in UTF-8 bytes (#533).
#:
#: Claude Code does not hand the agent a hook's ``additionalContext`` of
#: 10,000 characters or more: it saves it to a file and puts a 2 KB preview in
#: context. Over every transcript on the maintainer's machine on 2026-09-28
#: (``tools/measure_persist_threshold.py``), the largest hook text kept inline
#: was 9,872 characters and the smallest persisted one 10,044, with no overlap,
#: on every Claude Code version from 2.1.241 to 2.1.283. 28 of 343 envelopes in
#: the month before were persisted, and ``[mnemo learned]`` survived the
#: preview in none of them.
#:
#: Counted in bytes, which are never fewer than characters, so the cap holds
#: whichever of the two Claude Code counts, with 10% to spare.
ENVELOPE_MAX_BYTES = 9000


def utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))
