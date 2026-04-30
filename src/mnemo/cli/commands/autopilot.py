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
        "digest": _do_digest,
        "collect-misses": _do_collect_misses,
        "self-fix": _do_selffix,
    }.get(action)
    if handler is None:
        print("usage: mnemo autopilot {on,off,pause,status,digest,collect-misses,self-fix}")
        return 2
    return handler(args)


def _do_on(args: argparse.Namespace) -> int:
    from mnemo.autopilot.core.kill_switch import set_state
    from mnemo.autopilot.core.frozen_recall import freeze_current
    from mnemo.autopilot.core.labels import ensure_label_exists
    from mnemo.autopilot.core.dispatcher import schedule_autopilot_job

    vault = _vault()
    set_state(vault_root=vault, state="on", source="cli")
    # bootstrap frozen recall if recall-cases.json exists; ignore otherwise
    try:
        freeze_current(vault_root=vault)
    except FileNotFoundError:
        pass
    ensure_label_exists()

    # Register Tier 0 scheduled jobs
    schedule_autopilot_job(
        vault_root=vault,
        name="autopilot.tier0.digest",
        cron="0 9 * * 1",
        command="mnemo autopilot digest --post",
    )
    schedule_autopilot_job(
        vault_root=vault,
        name="autopilot.tier0.collect-misses",
        cron="0 8 * * *",
        command="mnemo autopilot collect-misses",
    )
    # Register Tier 1 self-fix scheduled jobs
    schedule_autopilot_job(
        vault_root=vault,
        name="autopilot.tier1.doctor",
        cron="0 10 * * 1",
        command="mnemo autopilot self-fix doctor",
    )
    schedule_autopilot_job(
        vault_root=vault,
        name="autopilot.tier1.sweep",
        cron="0 11 1 * *",
        command="mnemo autopilot self-fix sweep",
    )
    schedule_autopilot_job(
        vault_root=vault,
        name="autopilot.tier1.telemetry",
        cron="0 12 * * 0",
        command="mnemo autopilot self-fix telemetry",
    )
    schedule_autopilot_job(
        vault_root=vault,
        name="autopilot.tier1.poll-outcomes",
        cron="0 9 * * *",
        command="mnemo autopilot self-fix poll-outcomes",
    )

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


def _do_selffix(args: argparse.Namespace) -> int:
    from mnemo.cli.commands.selffix import cmd_selffix
    return cmd_selffix(args)


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


def _parse_since_days(since_str: str) -> int:
    """Parse a ``<N>d`` string into an integer number of days (default 7)."""
    since_str = (since_str or "7d").strip().lower()
    if since_str.endswith("d"):
        try:
            return max(1, int(since_str[:-1]))
        except ValueError:
            pass
    try:
        return max(1, int(since_str))
    except ValueError:
        return 7


def _do_digest(args: argparse.Namespace) -> int:
    from mnemo.autopilot.insights.digest import (
        generate_digest,
        write_digest,
        post_digest_issue,
    )

    vault = _vault()
    since_days = _parse_since_days(getattr(args, "since", "7d") or "7d")
    digest = generate_digest(vault_root=vault, since_days=since_days)
    path = write_digest(vault_root=vault, digest=digest)
    print(str(path))

    if getattr(args, "post", False):
        issue_num = post_digest_issue(digest=digest)
        if issue_num is not None:
            print(f"issue created: #{issue_num}")
        else:
            print("issue: (not created — gh unavailable or error)")

    return 0


def _do_collect_misses(args: argparse.Namespace) -> int:
    from mnemo.autopilot.insights.miss_collector import collect_recall_misses

    vault = _vault()
    count = collect_recall_misses(vault_root=vault)
    print(f"{count} new proposal(s) written")
    return 0
