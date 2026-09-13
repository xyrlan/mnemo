"""``mnemo session <short_id>`` — what one background session has been doing.

Layer 2 of the activity view. The queue gives one line per session so the
maintainer can scan; this gives the last N actions of one session, for when
that line looks wrong and the question becomes "wrong how".

No ``--follow``. You look, you decide, and you go to ``claude attach`` if you
want to be inside it — attach is Claude Code's job, not this one's.

Human-only, like the queue: no hook and no MCP tool exposes it. The parent
session's context is the scarce resource the whole feature protects.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command

NO_ACTIONS = "  nenhuma ação registrada na janela lida"


def _find(sessions, short_id: str):
    """Exact match first, then a unique prefix. None when neither resolves.

    An ambiguous prefix resolves to nothing rather than picking one: showing
    the wrong session's actions is worse than asking for another character.
    """
    for s in sessions:
        if s.short_id == short_id:
            return s
    matches = [s for s in sessions if s.short_id.startswith(short_id)]
    return matches[0] if len(matches) == 1 else None


def _clock(at):
    """``14:02:11`` from an ISO timestamp, or '' when it is unusable."""
    if not isinstance(at, str) or "T" not in at:
        return ""
    return at.split("T", 1)[1].split(".")[0].replace("Z", "")[:8]


@command("session")
def cmd_session(args: argparse.Namespace) -> int:
    """Print the recent actions of one background session."""
    from mnemo.core.activity import read_tail, recent_actions
    from mnemo.core.sessions.jobs import read_sessions

    short_id = str(getattr(args, "short_id", "") or "")
    limit = int(getattr(args, "limit", 15) or 15)

    session = _find(read_sessions(cwd=None), short_id)
    if session is None:
        print(f"sessão não encontrada: {short_id}")
        print("  listar: mnemo sessions --all")
        return 1

    flags = [f for f in (session.state, session.tempo) if f]
    if session.live is True:
        flags.append("live")
    elif session.live is False:
        flags.append("morta")
    print(f"{session.short_id}  {session.label}  {' · '.join(flags)}")
    if session.cwd:
        print(session.cwd)
    print("")

    if not session.link_scan_path:
        print("  esta sessão não registrou um transcript (linkScanPath ausente)")
        return 0

    events, _ = read_tail(session.link_scan_path, 0)
    actions = recent_actions(events, limit=limit)

    if not actions:
        print(NO_ACTIONS)
        return 0

    # A loop is the case this view exists to surface, and it is not always a
    # *consecutive* repeat: `Activity.repeated` only flags back-to-back tool
    # uses, but a grep run four times over with other work between each run
    # is exactly the pattern a maintainer scrolling this list needs flagged.
    # So the mark here is "seen again anywhere in the shown window", which is
    # a superset of `repeated` rather than a replacement for it.
    seen = set()  # type: set
    for act in actions:
        key = (act.tool, act.target)
        looped = act.repeated or key in seen
        seen.add(key)
        mark = "  ↻" if looped else ""
        print(f"  {_clock(act.at):>8}  {act.tool or '?':<12}  {act.target or '':<40}{mark}")

    print("")
    print(f"  attach: claude attach {session.short_id}")
    return 0
