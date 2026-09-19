"""Put a stalled child back to work without granting it anything (#393).

The manual recovery on 2026-09-19 was two commands per child: ``claude
respawn <id>`` to bring the process back, then a cross-session message
telling it the limit had reset and to check ``git status``. It worked, and
the issue is right that it worked by luck — a socket message is framed to the
child as a *peer*, and a peer "cannot grant escalation". It carried no
authority, and none was needed, because the child's grant was already in its
opening prompt.

One command does both halves, and was measured rather than assumed
(``resume-under-own-id``, 2.1.278, 2026-09-19)::

    claude --bg --resume <FULL session id> '<text>'

wakes the stopped session **under its own short id**, in its own job
directory, its own transcript and its own ``cwd``, with every saved option
restored — a probe child spawned with ``--disallowedTools Edit Write
NotebookEdit --setting-sources project,local --strict-mcp-config
--mcp-config … --settings … --model haiku`` came back carrying all of them,
so a read-only child does not quietly regain ``Edit`` and a lean child does
not quietly lose mnemo's hooks. ``SessionStart`` fires again on the wake.

**The trap, and why it is a guard and not a comment.** Passing the *short*
id instead of the full one does not fail. It prints ``note: started a copy of
that conversation as <new id>`` on stdout and runs a **second session in the
same worktree** — the one thing dispatch exists to prevent, and the shape of
the three git accidents in :mod:`mnemo.core.dispatch`'s docstring. Six
children woken that way would be six copies racing six originals over six
trees. :func:`wake` therefore refuses anything that is not a full id, and
checks the output for the copy note afterwards.

**What may ride this channel.** Nothing. A ``--bg --resume`` prompt lands in
the transcript as ``origin.kind: "human"``, ``promptSource: "typed"`` —
byte-for-byte the shape of the opening prompt, and so indistinguishable from
the maintainer typing. That is exactly why :data:`NUDGE` is a constant and
:func:`wake` takes no message: there is no argument through which a caller,
a script or a later feature could put words in the maintainer's mouth. The
text points back at the opening prompt and says, in as many words, that it
grants nothing.

It opens with :data:`NUDGE_PREFIX` for the second reason
:mod:`mnemo.core.sessions.inbox`'s notice does: ``detector.is_human_turn``
skips a turn that starts with one of its ``SYNTHETIC_PREFIXES``, and without
that every wake would be recorded as the maintainer answering a blocked
child — inflating the unblock population with edges no person was at.
"""
from __future__ import annotations

import subprocess
from typing import Optional

#: Opens the wake turn. In ``detector.SYNTHETIC_PREFIXES``, pinned by a test.
NUDGE_PREFIX = "<mnemo-resume"

#: The whole of what a woken child is told. A constant, not a template: see
#: "What may ride this channel" above. The only interpolation is the closing
#: tag, so the turn reads as one block rather than trailing off.
NUDGE = f"""{NUDGE_PREFIX}>
The account limit that stopped you mid-turn has reset, and you have been
woken with your conversation intact.

This message grants you nothing. Your opening prompt is still the only
instruction you have and the only thing that says what you may publish; it
has not changed, and neither has your task.

Your worktree still holds whatever you had written when the turn was cut —
run `git status` there first, then carry on from where you stopped.
</mnemo-resume>"""

#: A full Claude Code session id: the uuid ``state.json`` records as
#: ``sessionId``, whose first eight characters name the job directory.
_FULL_ID_LENGTH = 36

#: What the CLI prints on stdout when it forked instead of waking. Matched so
#: a copy is *reported*, never silently left running in a child's worktree.
_COPY_NOTE = "started a copy"


def is_full_session_id(value: Optional[str]) -> bool:
    """True when *value* is a full session id rather than a short one.

    Shape, not a uuid parse: Claude Code's own note says "lowercase, as
    ``claude agents --json`` prints it", and the failure this guards against
    is an eight-character short id being passed by mistake.
    """
    if not isinstance(value, str) or len(value) != _FULL_ID_LENGTH:
        return False
    if [i for i, c in enumerate(value) if c == "-"] != [8, 13, 18, 23]:
        return False
    return all(c in "0123456789abcdef-" for c in value)


def wake(session_id: str, *, cwd: str, timeout: float = 120.0) -> Optional[str]:
    """Wake *session_id* and hand it :data:`NUDGE`. ``None``, or why not.

    A message rather than an exception, like
    :func:`delivery.stop_session`: waking N children is a loop, and one that
    could not be woken must not take the other N-1 with it.

    *cwd* is the child's own worktree. ``--resume`` restores the session's
    recorded ``cwd`` regardless, but running from anywhere else would make a
    failed wake spawn a fresh session in the wrong tree.
    """
    if not is_full_session_id(session_id):
        return (
            f"{session_id!r} is not a full session id; `claude --bg --resume` "
            "would start a *copy* of the conversation in the same worktree "
            "rather than waking it (claude_cli assumption `resume-under-own-id`)"
        )
    try:
        result = subprocess.run(
            ["claude", "--bg", "--resume", session_id, NUDGE],
            cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if result.returncode != 0:
        return (result.stderr.strip() or result.stdout.strip()
                or f"exit {result.returncode}")
    if _COPY_NOTE in (result.stdout or ""):
        # The id passed the shape check and the CLI forked anyway: the
        # assumption has moved. Say so with the bytes, and name the copy —
        # it is running in the child's tree right now.
        from mnemo.core import claude_cli

        return str(claude_cli.ContractBroken(
            "resume-under-own-id",
            f"`claude --bg --resume {session_id}` started a copy instead of "
            f"waking the session; stdout was {result.stdout.strip()!r}",
        ))
    return None
