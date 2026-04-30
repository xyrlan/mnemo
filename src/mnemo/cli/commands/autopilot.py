"""``mnemo autopilot {on,off,pause,status}`` — autopilot kill switch + status."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mnemo.cli.parser import command


def _vault() -> Path:
    from mnemo import cli  # late binding for monkeypatched _resolve_vault
    return cli._resolve_vault()


@command("autopilot")
def cmd_autopilot(args: argparse.Namespace) -> int:
    action = getattr(args, "autopilot_action", None)
    handler = {
        "on": _do_on,
        "off": _do_off,
        "pause": _do_pause,
        "status": _do_status,
    }.get(action)
    if handler is None:
        print("usage: mnemo autopilot {on,off,pause,status}")
        return 2
    return handler(args)


def _do_on(args: argparse.Namespace) -> int:
    from mnemo.autopilot.core.kill_switch import set_state
    from mnemo.autopilot.core.frozen_recall import freeze_current
    from mnemo.autopilot.core.labels import ensure_label_exists

    vault = _vault()
    set_state(vault_root=vault, state="on", source="cli")
    # bootstrap frozen recall if recall-cases.json exists; ignore otherwise
    try:
        freeze_current(vault_root=vault)
    except FileNotFoundError:
        pass
    ensure_label_exists()
    print("autopilot: on")
    return 0


def _do_off(args: argparse.Namespace) -> int:
    from mnemo.autopilot.core.kill_switch import set_state
    from mnemo.autopilot.core.dispatcher import (
        list_autopilot_jobs,
        cancel_autopilot_job,
    )

    vault = _vault()
    for job in list_autopilot_jobs(vault_root=vault):
        cancel_autopilot_job(vault_root=vault, name=job.name)
    set_state(vault_root=vault, state="off", source="cli")
    print("autopilot: off")
    return 0


def _do_pause(args: argparse.Namespace) -> int:
    from mnemo.autopilot.core.kill_switch import set_state

    vault = _vault()
    hours = max(1, int(getattr(args, "hours", 24) or 24))
    until = (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    set_state(vault_root=vault, state="paused", paused_until=until, source="cli")
    print(f"autopilot: paused for {hours}h (until {until})")
    return 0


def _do_status(args: argparse.Namespace) -> int:
    from mnemo.autopilot.core.kill_switch import get_state, is_active
    from mnemo.autopilot.core.dispatcher import list_autopilot_jobs
    from mnemo.autopilot.core._dirs import autopilot_budget_path
    import json

    vault = _vault()
    state = get_state(vault_root=vault)
    active = is_active(vault_root=vault)
    print(f"State: {state} ({'active' if active else 'inactive'})")

    bp = autopilot_budget_path(vault)
    if bp.exists():
        data = json.loads(bp.read_text())
        print(f"Budget window start: {data.get('window_start')}")
        counts = data.get("counts", {})
        if counts:
            print("Counts today:")
            for k, v in sorted(counts.items()):
                print(f"  {k}: {v}")
        else:
            print("Counts today: (none)")
        recent = data.get("recent_outcomes", [])
        if recent:
            print("Recent outcomes:")
            for o in recent[-5:]:
                print(f"  PR #{o['pr']}: {o['outcome']} @ {o['ts']}")
    else:
        print("Budget: (no activity yet)")

    jobs = list_autopilot_jobs(vault_root=vault)
    if jobs:
        print("Scheduled jobs:")
        for j in jobs:
            print(f"  {j.name}  cron={j.cron}  cmd={j.command}")
    else:
        print("Scheduled jobs: (none)")
    return 0
