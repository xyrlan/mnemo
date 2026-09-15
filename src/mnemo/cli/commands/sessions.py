"""``mnemo sessions`` — the live queue of Claude Code background sessions.

Six sessions in parallel cost six streams of attention unless something says
which ones are actually waiting. This prints that, blocked first.

Deliberately human-only: no hook and no MCP tool exposes it. The queue must
never spend a token of the parent session's context — that context is the
scarce resource the whole feature exists to protect.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command
from mnemo.core.activity import activities_for


def _consume_unblocks() -> int:
    """``mnemo sessions --consume-unblocks`` — redeem the recorded markers.

    The production reader ``detector.pending_unblocks`` never had (#195). The
    ``session_end`` hook spawns this detached; a maintainer can also run it by
    hand, which is why it reports rather than staying silent.

    Unscoped, like the sweep that records the markers: an unblocked session is
    worth learning from wherever it ran.
    """
    from mnemo import cli  # late binding, as the queue path does
    from mnemo.core import config as cfg_mod
    from mnemo.core.sessions import unblocks

    report = unblocks.consume(cfg_mod.load_config(), vault_root=cli._resolve_vault())

    if not (report.consumed or report.failed or report.skipped):
        print("no unblocked session waiting to be learned from")
        return 0

    print(f"consumed: {report.consumed} unblocked session(s)")
    for entry in report.learned:
        print(f"learned: {entry.get('slug')} — {entry.get('name')}")
    if report.skipped:
        print(f"skipped: {report.skipped} marker(s) with no resolvable transcript")
    for line in report.errors:
        # Left pending on purpose: these are retried on the next pass.
        print(f"deferred: {line}")
    return 0


def _append_watch(read, activities, interval: float) -> None:
    """Watch by appending only what changed, never clearing the screen.

    The default redraw reprints every row on every tick. Measured against six
    real dispatch children over 60 seconds: 114 row-renders carried 7 actual
    changes — 6.1%. The other 94% is the noise that makes scrollback useless
    and hides the one row that moved.

    So the unit here is the *event*, not the frame: a line is emitted when a
    session's activity differs from the last one printed for it, and nothing
    is emitted otherwise. That makes the output readable in a log, beside the
    work it describes, and greppable after the fact — none of which the
    redraw can offer.

    ``(tool, target, at)`` is the identity. Comparing the rendered string
    instead would go quiet on a session that repeats a tool, which is exactly
    the loop the ``↻`` mark exists to surface.
    """
    import time

    from mnemo.core.sessions.render import _activity, _label

    last: dict = {}

    while True:
        found = read()
        acts = activities(found)

        for session in found:
            short_id = getattr(session, "short_id", None)
            act = acts.get(short_id) if short_id else None
            if act is None or not act.tool:
                continue
            key = (act.tool, act.target, act.at)
            if last.get(short_id) == key:
                continue
            last[short_id] = key
            # _label is the queue's own budgeted label, so an appended line
            # and a table row name the same session the same way.
            print(f"{_clock(act.at):>8}  {short_id}  {_label(session)}  "
                  f"{_activity(act)}".rstrip(), flush=True)

        time.sleep(interval)


def _explorations(found, cache: dict, *, only_done: bool = True) -> dict:
    """What each session spent before its first edit, by ``short_id`` (#269).

    *cache* is carried across watch ticks. A finished session's number cannot
    change — its transcript is closed — so it is read once per process and not
    once per redraw. A running session's is re-read each call, and never cached.

    Never raises: a transcript that cannot be read costs its own cell, not the
    queue.
    """
    from mnemo.core.activity import exploration_for

    out = {}
    for session in found:
        short_id = getattr(session, "short_id", None)
        if not short_id or not getattr(session, "link_scan_path", None):
            continue
        done = bool(getattr(session, "is_done", False))
        if only_done and not done:
            continue
        key = (short_id, session.link_scan_path)
        if key in cache:
            out[short_id] = cache[key]
            continue
        try:
            measured = exploration_for(session.link_scan_path, session.cwd)
        except Exception:
            measured = None
        if measured is None:
            continue
        if done:
            cache[key] = measured
        out[short_id] = measured
    return out


def _clock(at) -> str:
    """``14:02:11`` from an ISO timestamp, or '' when it is unusable.

    Mirrors ``commands.session._clock``. Not imported from it: that module is
    the detail view and importing it here would pull a second command's
    module in just for eight characters of formatting.
    """
    if not isinstance(at, str) or "T" not in at:
        return ""
    return at.split("T", 1)[1].split(".")[0].replace("Z", "")[:8]


def _without_stale(found) -> list:
    """*found* minus the finished sessions whose tree is gone (#292).

    ``getattr``, not the attribute: the command's tests hand it bare objects,
    and a row that cannot say it is stale is shown.
    """
    return [s for s in found if not getattr(s, "is_stale", False)]


def _stale_footer(hidden: int) -> str:
    """The count of what ``--stale`` would add back, or '' when nothing was hidden.

    Printed rather than dropped silently for the reason #281 gave the empty
    queue: an omission nobody is told about reads as the whole truth.
    """
    if not hidden:
        return ""
    return f"  {hidden} prontas com a árvore já removida (mnemo sessions --stale)"


@command("sessions")
def cmd_sessions(args: argparse.Namespace) -> int:
    """Print background sessions, blocked first."""
    import json as _json
    import os
    import sys
    import time
    from dataclasses import asdict

    from mnemo.core.sessions.jobs import normalize_cwd, read_sessions
    from mnemo.core.sessions.render import render_queue

    if bool(getattr(args, "consume_unblocks", False)):
        return _consume_unblocks()

    # Normalized here too: the stored cwd may be the canonical form while the
    # live one arrives through a worktree symlink. See jobs.normalize_cwd.
    # The scope is a repo and its dispatch worktrees, not one directory — see
    # jobs.in_scope for why equality hid every child this tool exists for.
    scope = None if getattr(args, "all", False) else normalize_cwd(os.getcwd())
    show_stale = bool(getattr(args, "stale", False))
    # How many rows the last read hid, for the footer. A one-element list for
    # the same reason as `previous` below: `_read` replaces it on every tick.
    hidden = [0]

    # path -> (offset, meter), carried across watch ticks so a running child
    # costs the bytes it appended since and a finished one costs a stat.
    contexts: dict = {}

    def _read():
        everything = read_sessions(cwd=scope)
        found = everything if show_stale else _without_stale(everything)
        hidden[0] = len(everything) - len(found)
        try:
            from dataclasses import replace

            from mnemo.core.activity.context import measure

            # How full each session is (#307) and what filled it (#308), from
            # one read of the transcript: state.json's `tokens` is not the
            # size, and `/context`'s per-tool figures are not the breakdown.
            measured = [measure(s.link_scan_path, contexts) for s in found]
            found = [replace(s, context_tokens=size,
                             context_breakdown=None if size is None else tools)
                     for s, (size, tools) in zip(found, measured)]
        except Exception:
            pass
        try:
            from mnemo import cli  # late binding for monkeypatched _resolve_vault

            vault = cli._resolve_vault()
        except Exception:
            return found  # the queue must print even when the vault is unavailable
        try:
            from mnemo.core.sessions import detector

            # Every session, stale ones included: a child's last answer can
            # still be sitting unswept in its transcript after its tree is gone.
            detector.sweep(everything, vault_root=vault)
        except Exception:
            pass
        try:
            from mnemo.core.sessions import parents

            # Who dispatched each child (#288). Separate from the sweep so a
            # failure in one never costs the other.
            found = parents.stamp(found, vault_root=vault)
        except Exception:
            pass
        return found

    if bool(getattr(args, "json", False)):
        # The derived booleans ride along with the raw fields: `asdict` cannot
        # see a @property, so without this every consumer re-implements the
        # blocked/waiting rule and gets it wrong the way #222 did. Additive,
        # so a consumer reading state/tempo/live is untouched.
        found = _read()
        # Every session, not only finished ones: a JSON consumer summing a
        # dispatch reads `reached` to tell a final count from a running floor.
        spent = _explorations(found, {}, only_done=False)
        rows = [
            {**asdict(s), **s.derived(),
             "exploration": spent[s.short_id].as_dict() if s.short_id in spent else None}
            for s in found
        ]
        print(_json.dumps(rows, indent=2, ensure_ascii=False))
        return 0

    # In memory, for the life of this process. An offset only has value inside
    # a live watch; a single invocation wants current state, not a delta.
    #
    # `previous` is a one-element list, not a bare dict, because `_activities`
    # both reads and replaces it on every tick. A plain name would need
    # `nonlocal`; the list keeps the closure honest with one fewer keyword.
    offsets = {}
    previous = [{}]

    def _activities(found):
        """Never let a transcript read cost us the queue itself."""
        try:
            previous[0] = activities_for(found, offsets, previous=previous[0])
        except Exception:
            previous[0] = {}
        return previous[0]

    explored_cache = {}

    append = bool(getattr(args, "append", False))
    # --append is a watch mode, so it implies the loop. Requiring both flags
    # would make `--append` alone print once and exit, which reads as a no-op.
    watching = bool(getattr(args, "watch", False)) or append
    interval = float(getattr(args, "interval", 2.0) or 2.0)

    if watching:
        if append:
            try:
                _append_watch(_read, _activities, interval)
            except KeyboardInterrupt:
                pass
            return 0

        # Only a terminal understands the escape; redirected to a log it would
        # be raw bytes on every redraw.
        clear = "\033[2J\033[H" if sys.stdout.isatty() else ""  # clear + home
        try:
            while True:
                found = _read()
                acts = _activities(found)
                print(clear, end="")
                print(render_queue(found, acts, explorations=_explorations(found, explored_cache)))
                if hidden[0]:
                    print(_stale_footer(hidden[0]))
                time.sleep(interval)
        except KeyboardInterrupt:
            return 0

    found = _read()
    print(render_queue(found, _activities(found), explorations=_explorations(found, explored_cache)))
    if hidden[0]:
        print(_stale_footer(hidden[0]))
    if not found and scope is not None:
        # An empty scoped queue and an empty machine render identically, and
        # the first one is a lie by omission: #281 sat on four waiting
        # sessions — one blocked five hours — because the queue said nothing
        # and nothing reads as "nothing is running". Only the *count* is
        # printed; listing them here would quietly make `--all` the default.
        #
        # The second read costs one pass over the jobs dir, and only on the
        # path where there is nothing else to show.
        try:
            others = read_sessions()
            elsewhere = len(others if show_stale else _without_stale(others))
        except Exception:
            elsewhere = 0
        if elsewhere:
            print(f"  {elsewhere} em outros diretórios (mnemo sessions --all)")
    return 0
