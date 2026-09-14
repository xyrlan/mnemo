"""``mnemo session <short_id>`` — what one background session has been doing.

Layer 2 of the activity view. The queue gives one line per session so the
maintainer can scan; this gives the last N actions of one session, for when
that line looks wrong and the question becomes "wrong how".

Prints once by default: you look, you decide, and you go to ``claude attach``
if you want to be inside it — attach is Claude Code's job, not this one's.

``--follow`` (#218) keeps it open, appending new actions as they arrive. #210
ruled that out as a scope call; #218 revisited it because watching one child
accumulate actions turned out to be a thing the maintainer wanted, and the
bookmark that makes it cheap had already landed unused on this path.

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


def _line(act, looped: bool) -> str:
    """One action row. Shared so followed and one-shot lines cannot drift."""
    mark = "  ↻" if looped else ""
    return f"  {_clock(act.at):>8}  {act.tool or '?':<12}  {act.target or '':<40}{mark}"


def _follow(session, limit: int, interval: float) -> int:
    """Print new actions as they arrive, carrying the bookmark across ticks.

    #210 ruled ``--follow`` out as a scope call; #218 revisited it because
    watching one child accumulate actions is a thing the maintainer wanted.
    The plumbing was already there and unused on this path: ``read_tail``
    returns a new offset and this command passed 0 on every run, so each
    re-run paid a 256KB cold window to re-read what it had already shown.

    The bookmark is what makes this cheap *and* what makes it append-only —
    after the first tick a read returns only what is new, so an action is
    printed exactly once. No ``attach:`` footer: repeated every tick it would
    be noise in a stream whose whole value is that new lines mean new work.
    """
    import time

    from mnemo.core.activity import read_tail, recent_actions

    # The first window is the same one the one-shot view shows, so a follow
    # starts from context rather than from a blank screen; after that the
    # offset advances and only genuinely new actions arrive.
    events, offset = read_tail(session.link_scan_path, 0)
    actions = recent_actions(events, limit=limit)
    if not actions:
        print(NO_ACTIONS)

    seen = set()  # type: set
    try:
        while True:
            for act in actions:
                key = (act.tool, act.target)
                print(_line(act, act.repeated or key in seen), flush=True)
                seen.add(key)

            time.sleep(interval)
            events, offset = read_tail(session.link_scan_path, offset)
            # limit bounds the opening window only: once following, every new
            # action is shown, because dropping one would put a hole in the
            # stream the maintainer is reading to see progress.
            actions = recent_actions(events, limit=0) if events else []
    except KeyboardInterrupt:
        return 0


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

    # The model goes here rather than in the queue's table (#268): this view
    # is already the one that answers "that row looks wrong, wrong how", and
    # the model is one of the answers. The queue says it once in a footer
    # instead of once per row — see render._models.
    flags = [f for f in (session.state, session.tempo, session.model) if f]
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

    if bool(getattr(args, "follow", False)):
        return _follow(session, limit, float(getattr(args, "interval", 2.0) or 2.0))

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
        print(_line(act, looped))

    print("")
    print(f"  attach: claude attach {session.short_id}")
    return 0
