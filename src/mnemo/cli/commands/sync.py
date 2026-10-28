"""``mnemo sync`` — one vault on several of your own machines (#642).

    mnemo sync init <remote>    # on the machine that owns the vault
    mnemo sync join <remote>    # on another machine
    mnemo sync pull             # bring the hub in; the primary wins a conflict
    mnemo sync push             # commit the tracked set and push it
    mnemo sync status [--json]  # on or off, the role, what is waiting

Off until ``init`` or ``join`` is typed, and no hook calls any of it. The hub
is a private git remote the user already has; mnemo runs the machine's ``git``
and stores no credential. See :mod:`mnemo.core.sync` for what travels and how
a conflict is settled.
"""
from __future__ import annotations

import argparse
from typing import Any, Dict, Optional

from mnemo.cli.parser import command

_USAGE = """\
usage: mnemo sync <init REMOTE | join REMOTE | pull | push | status [--json]>

  init REMOTE   on the machine that owns the vault: make it the primary, push it
  join REMOTE   on another machine: put the hub's vault here (an existing one is moved aside)
  pull          bring the hub into the vault; the primary's version wins a conflict
  push          commit what changed in the tracked set and push it
  status        on or off, the role, what is waiting, the last pull and push"""


def _ago(seconds: Optional[int]) -> str:
    if seconds is None:
        return "never"
    for size, unit in ((86400, "d"), (3600, "h"), (60, "min")):
        if seconds >= size:
            return f"{seconds // size} {unit} ago"
    return f"{seconds} s ago"


def _when(stamp: Optional[str], seconds: Optional[int]) -> str:
    return f"{stamp} ({_ago(seconds)})" if stamp else "never"


def _print_status(s: Dict[str, Any]) -> None:
    trouble = s.get("stateProblem")
    if not s.get("enabled"):
        if trouble:
            # Not "off": a crash can zero the file of a vault that synced (#735).
            print("sync: unknown")
            print(f"  ! state:    {trouble}, and nothing in .git/ says this vault synced")
            print(f"  machine id: {s.get('machineId')}")
            print("  → if this machine synced, `mnemo sync join <remote>` again; "
                  "if it never did, delete that file")
            return
        print("sync: off")
        print(f"  machine id: {s.get('machineId')}")
        print("  → `mnemo sync init <remote>` on the machine that owns the vault, "
              "`mnemo sync join <remote>` on another")
        return
    print("sync: on")
    if trouble:
        print(f"  ! state:    {trouble}; sync carries on from what .git/ records, "
              "and the next pull or push writes it afresh")
    for item in s.get("gitLocks") or []:
        print(f"  ! git lock: {item['path']} left {_ago(item['ageSeconds'])} by a git that died; "
              "the next pull or push removes it")
    print(f"  remote:     {s['remote']}")
    print(f"  role:       {s['role']} (machine '{s['machineId']}', primary '{s['primary']}')")
    print(f"  waiting:    {s['pendingFiles']} file{'s' if s['pendingFiles'] != 1 else ''} to push, "
          f"{s['ahead']} commit{'s' if s['ahead'] != 1 else ''} ahead, {s['behind']} behind "
          "(as of the last fetch)")
    withheld = int(s.get("withheldFiles") or 0)
    if withheld:
        print(f"  withheld:   {withheld} change{'s' if withheld != 1 else ''} a satellite may not "
              "publish; they stay on this machine")
    print(f"  last pull:  {_when(s['lastPull'], s['lastPullAgoSeconds'])}")
    print(f"  last push:  {_when(s['lastPush'], s['lastPushAgoSeconds'])}")
    print(f"  conflicts:  {s['conflictsResolved']} resolved, {s['losersKept']} losing "
          f"version{'s' if s['losersKept'] != 1 else ''} kept")
    print(f"  refused:    {s['pushesRefused']} push{'es' if s['pushesRefused'] != 1 else ''}")
    for item in s["refused"]:
        print(f"    ! {item.get('path')}: {item.get('kind')} (the push is refused until it is gone)")
    if not s["formatSupported"]:
        print(f"  ! the hub is at sync format {s['hubFormatVersion']} and this mnemo knows "
              f"{s['formatVersion']}: it neither pulls nor pushes until mnemo is upgraded here")
    err = s["lastError"]
    print(f"  last error: {err['at']} {err['op']}: {err['message']}" if err else "  last error: none")


@command("sync")
def cmd_sync(args: argparse.Namespace) -> int:
    import json as _json

    from mnemo import cli
    from mnemo.core import config as cfg_mod
    from mnemo.core import sync

    action = getattr(args, "sync_action", None)
    if action is None:
        print(_USAGE)
        return 2

    vault = cli._resolve_vault()
    cfg = cfg_mod.load_config()
    machine = sync.machine_id(cfg)
    config_path = cfg_mod.default_config_path()
    if action == "hook-pull":
        # SessionStart's worker (#643): the pull, then what the primary does
        # with briefings it brought. Prints nothing; the hook waits on it.
        from mnemo.core.sync import hooks as sync_hooks

        try:
            sync_hooks.run_hook_pull(cfg, vault)
        except Exception:  # noqa: BLE001 — logged by the worker, never raised
            return 1
        return 0
    try:
        if action == "status":
            report = sync.status(vault, machine=machine)
            if getattr(args, "json", False):
                print(_json.dumps(report, indent=2))
            else:
                _print_status(report)
            return 0
        if action == "init":
            result = sync.init(vault, args.remote, machine=machine, config_path=config_path)
        elif action == "join":
            result = sync.join(vault, args.remote, machine=machine, config_path=config_path)
        elif action == "pull":
            result = sync.pull(vault, machine=machine, trigger="cli")
        else:
            result = sync.push(vault, machine=machine, config_path=config_path, trigger="cli")
    except sync.GitMissing:
        print("mnemo sync: git is not installed or not on PATH; sync runs this machine's own git")
        return 1
    except sync.GitError as exc:
        print(f"mnemo sync: {exc.stderr}")
        return 1
    print(f"mnemo sync {action}: {result.message}")
    return 0 if result.ok else 1
