"""``mnemo grok``: Grok Build sessions through the leader's ACP socket.

mnemo-desktop reads and acts on Grok sessions through these verbs, the way it
runs ``claude agents`` for Claude ones; it has no ACP client of its own. Each
verb is one :class:`mnemo.core.grok.acp.Leader` call
(``design/specs/2026-10-16-grok-host.md``, decision 4).

- ``roster [--home DIR] [--json]``: the live state of every session; without
  ``--home``, of every home in ``paths.grok_homes()`` that is logged in. A
  listing never starts a leader (#719): a home whose leader is not listening
  answers from its session files, every row ``dormant`` and not resident.
- ``send ID TEXT [--home DIR]``: a user turn; exits 0 once the turn started.
- ``close ID [--home DIR]``: prints Grok's outcome.
- ``billing [--home DIR] [--json]``: ``_x.ai/billing``, unchanged.

``ID`` is a full session id or its ``paths.short_id``. A verb that cannot
reach the session or the leader says why on stderr and exits 1.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from mnemo.cli.parser import command


def _home(args: argparse.Namespace) -> Optional[Path]:
    return Path(args.home).expanduser() if getattr(args, "home", None) else None


def _fail(text: str, code: int = 1) -> int:
    print(f"mnemo grok: {text}", file=sys.stderr)
    return code


def _row(entry: Any) -> Dict[str, Any]:
    from mnemo.core.grok import paths

    return {
        "session_id": entry.session_id,
        "short_id": paths.short_id(entry.session_id),
        "cwd": entry.cwd,
        "activity": entry.activity,
        "resident": entry.resident,
        "model_id": entry.model_id,
        "effort": entry.effort,
        "title": entry.title,
        "last_change_ms": entry.last_change_ms,
        "home": str(entry.home),
    }


def _entries(home: Path) -> List[Any]:
    """``home``'s roster from its leader if one is listening, else from its disk.

    ``Leader.roster`` goes through ``grok agent --leader stdio``, which spawns
    a leader when none listens; the desktop asks every 3 s for every account,
    and a listing must not leave a daemon behind. ``send`` and ``close`` are
    actions and still start one.
    """
    from mnemo.core.grok import acp
    from mnemo.core.sessions.grok_sessions import leader_listening

    if not home.is_dir():
        raise acp.GrokUnavailable(f"Grok home {home} does not exist")
    if leader_listening(home) is True:
        return acp.Leader(home).roster()
    return acp.disk_roster(home)


def _roster(args: argparse.Namespace) -> int:
    from mnemo.core.grok import acp, paths

    explicit = _home(args)
    homes = [explicit] if explicit is not None else paths.grok_homes()
    rows: List[Dict[str, Any]] = []
    tried = failed = 0
    for home in homes:
        if explicit is None and not acp.has_login(home):
            continue  # an account folder nobody logged in to: nothing to list
        tried += 1
        try:
            rows.extend(_row(e) for e in _entries(home))
        except (acp.GrokUnavailable, TimeoutError) as exc:
            failed += 1
            print(f"mnemo grok roster: {home}: {exc}", file=sys.stderr)
    if args.json:
        print(json.dumps(rows, indent=2))
    elif not rows:
        print("no Grok sessions")
    else:
        for row in rows:
            title = row["title"] or ""
            print(f"{row['short_id']}  {row['activity']:<11} {row['cwd']}  {title}".rstrip())
    # Without --home, one account's leader being down does not hide the others.
    return 1 if tried and failed == tried else 0


def _resolve(args: argparse.Namespace, verb: str):
    """``(leader, full session id, its cwd)`` for ``ID``, or an exit code."""
    from mnemo.core.grok import acp

    if not args.grok_args:
        return _fail(f"{verb} needs a session id", 2)
    leader = acp.Leader(_home(args))
    found = acp.find_session(args.grok_args[0], leader.home)
    if found is None:
        return _fail(f"no Grok session {args.grok_args[0]} in {leader.home}")
    cwd = acp.session_cwd(found)
    if cwd is None:
        return _fail(f"cannot tell which directory Grok session {found.name} belongs to")
    return leader, found.name, cwd


def _send(args: argparse.Namespace) -> int:
    from mnemo.core.grok import acp

    if len(args.grok_args) < 2:
        return _fail("send needs a session id and the text to send", 2)
    resolved = _resolve(args, "send")
    if isinstance(resolved, int):
        return resolved
    leader, session_id, cwd = resolved
    text = " ".join(args.grok_args[1:])
    try:
        leader.prompt(session_id, cwd, text, wait=False, timeout=120)
    except (acp.GrokUnavailable, TimeoutError) as exc:
        return _fail(str(exc))
    if args.json:
        print(json.dumps({"session_id": session_id, "started": True}))
    return 0


def _close(args: argparse.Namespace) -> int:
    from mnemo.core.grok import acp

    resolved = _resolve(args, "close")
    if isinstance(resolved, int):
        return resolved
    leader, session_id, _cwd = resolved
    try:
        outcome = leader.close(session_id)
    except (acp.GrokUnavailable, TimeoutError) as exc:
        return _fail(str(exc))
    print(json.dumps({"session_id": session_id, "outcome": outcome}) if args.json else outcome)
    return 0


def _billing(args: argparse.Namespace) -> int:
    from mnemo.core.grok import acp

    leader = acp.Leader(_home(args))
    try:
        result = leader.billing()
    except (acp.GrokUnavailable, TimeoutError) as exc:
        return _fail(str(exc))
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    config = result.get("config") or {}
    tier = result.get("subscription_tier") or result.get("subscriptionTier") or "unknown tier"
    used = config.get("creditUsagePercent")
    period = (config.get("currentPeriod") or {}).get("end") or config.get("billingPeriodEnd")
    line = f"{leader.home}: {tier}"
    if used is not None:
        line += f", {used:g}% of credit used"
    if period:
        line += f", period ends {period}"
    print(line)
    return 0


_VERBS = {"roster": _roster, "send": _send, "close": _close, "billing": _billing}


@command("grok")
def cmd_grok(args: argparse.Namespace) -> int:
    return _VERBS[args.grok_action](args)
