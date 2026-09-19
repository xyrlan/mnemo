"""How late a hook-driven wake would have been, per real rate limit (#396).

Usage:
    PYTHONPATH=src python3 tools/measure_wake_latency.py [--projects ~/.claude/projects] [--vault ~/mnemo] [--json]

Read-only: no LLM calls, no writes, no behaviour change.

#396 asks for a trigger that wakes a rate-limited child once the clock has
freed it, and leaves the choice of trigger open. mnemo's existing triggers are
all hooks — ``SessionStart`` and ``SessionEnd`` — so the first question is not
"is a hook pass safe" but "would a hook pass ever *fire*". This measures that,
and it is the number the answer turns on.

**The two things it reads.**

- Every ``rate_limit`` API error carrying ``quotaLimits.resetsAt`` in the
  transcripts under ``--projects``, classified by
  :mod:`mnemo.core.sessions.stalls` — the same classifier the wake uses, so
  the population here is exactly the population a trigger would act on.
- Every ``🟢 session started`` / ``🔴 session ended`` line in the vault's day
  logs (``bots/*/logs/<date>.md``). Those lines are written *by* mnemo's
  SessionStart and SessionEnd hooks, so they are a record of when a hook
  actually ran on this machine — the only such record that survives the
  session itself.

**What it reports, per reset epoch.**

- ``latency_minutes`` — from the reset to the first hook event at or after it.
  That is exactly how long a SessionStart/SessionEnd-driven pass would have
  left the child stalled past the moment it was free.
- ``armed`` — whether any hook fired *between* the stall and its reset. A
  trigger that reacts to the stall by setting a timer has to be started in
  that window, and this says whether anything was there to start it.
- ``hook_before_minutes`` — how long before the stall the last hook ran. A
  watcher does not have to be armed by the stall: it only has to be alive when
  the reset comes, and it is started before the stall by whatever session was
  running then.

**The result on the maintainer's machine (2026-09-19, 525 transcripts,
5372 recorded hook events).** 15 rate limits carry an epoch, over 5 distinct
resets; 8 of the 15 are dispatched worktrees (``<repo>-wt-<issue>``), which is
the population a wake acts on.

- A hook-driven pass would have fired **15, 109, 229 and 499 minutes** past
  the reset (the fifth reset is still in the future).
- ``armed`` is False for **4 of the 5**. At the 02:40 reset of 2026-09-19 —
  the incident #396 was written about — eight sessions were stalled at once:
  the six children of #380-#385 and two unrelated ones. Everything that was
  running had been stopped by the same limit, and an idle session fires no
  hook, so no hook of any kind ran between 00:43 and 06:29. The 06:29 event
  that ends the gap **is** the maintainer's own manual recovery. A hook pass
  would have saved that incident nothing at all.
- ``hook_before_minutes`` is **9 to 86** across those four: a session was
  running minutes before each stall, so a watcher started at its SessionStart
  would still have been alive when the clock came round.

So the limit that stalls the children also silences the only thing that could
notice, and the trigger has to be something that was already running and needs
no API of its own to stay running. That is what #396 ships
(:mod:`mnemo.core.sessions.rewake`): a hook pass for the cheap case, and a
watcher the hook starts *before* anything stalls for the case the hook cannot
reach.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from bisect import bisect_left
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mnemo.core.sessions.stalls import Kind, from_record  # noqa: E402

#: A day-log line mnemo's own hooks wrote. The file name carries the date and
#: the line carries local wall-clock, which is how ``log_writer`` writes them.
_LOG_LINE = re.compile(r"^- \*\*(\d{2}):(\d{2})\*\* — (?:🟢 session started|🔴 session ended)")
_LOG_NAME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")


def read_hook_events(vault_root: str) -> List[float]:
    """Unix epochs at which a mnemo SessionStart or SessionEnd hook ran.

    Sorted, deduplicated to nothing — two sessions starting in the same minute
    are two events, because each one is a chance for a pass to fire.

    Minute resolution, which is the day log's. That rounds a latency down by
    under a minute and never changes which side of an hours-long gap an event
    falls on, so it cannot flatter or damage the answer this tool exists for.
    """
    out: List[float] = []
    logs = os.path.join(vault_root, "bots", "*", "logs", "*.md")
    import glob

    for path in glob.glob(logs):
        name = _LOG_NAME.match(os.path.basename(path))
        if not name:
            continue
        year, month, day = (int(x) for x in name.groups())
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    hit = _LOG_LINE.match(line)
                    if not hit:
                        continue
                    hour, minute = int(hit.group(1)), int(hit.group(2))
                    try:
                        moment = datetime(year, month, day, hour, minute)
                    except ValueError:
                        continue
                    out.append(moment.astimezone().timestamp())
        except OSError:
            continue
    out.sort()
    return out


def _records(path: str) -> Iterable[Dict[str, Any]]:
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                if b'"isApiErrorMessage"' not in raw:
                    continue
                try:
                    record = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def _epoch(stamp: Any) -> Optional[float]:
    """An ISO-8601 ``…Z`` transcript timestamp as a unix epoch, or ``None``.

    Stamped UTC by Claude Code, and read as UTC here rather than parsed naive
    and corrected afterwards: a naive parse is read back as *local* time, which
    moved every stall three hours on the machine this was written on and put
    one of them after its own reset.
    """
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def read_stalls(projects_root: str) -> Tuple[List[Dict[str, Any]], int]:
    """``(rate limits carrying a reset epoch, transcripts scanned)``.

    Classified by :func:`mnemo.core.sessions.stalls.from_record`, so a
    ``rate_limit`` with no epoch — the per-model credit cap — is not here, for
    the same reason the wake never touches one: no clock frees it.
    """
    rows: List[Dict[str, Any]] = []
    scanned = 0
    try:
        dirs = sorted(os.scandir(projects_root), key=lambda d: d.name)
    except OSError:
        return rows, 0
    for entry in dirs:
        if not entry.is_dir():
            continue
        try:
            names = sorted(os.listdir(entry.path))
        except OSError:
            continue
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            scanned += 1
            path = os.path.join(entry.path, name)
            for record in _records(path):
                stall = from_record(record)
                if stall is None or stall.kind != Kind.RATE_LIMIT:
                    continue
                if stall.resets_at is None:
                    continue
                rows.append({
                    "session": name[:-len(".jsonl")],
                    "at": _epoch(record.get("timestamp")),
                    "resets_at": stall.resets_at,
                    "limit": stall.limit,
                })
    return rows, scanned


def first_event_after(events: List[float], moment: float) -> Optional[float]:
    """The first hook event at or after *moment*, or ``None``."""
    index = bisect_left(events, moment)
    return events[index] if index < len(events) else None


def any_event_between(events: List[float], start: float, end: float) -> bool:
    """True when a hook ran in ``[start, end)`` — the window a timer could arm in."""
    index = bisect_left(events, start)
    return index < len(events) and events[index] < end


def last_event_before(events: List[float], moment: float) -> Optional[float]:
    """The last hook event strictly before *moment*, or ``None``.

    The other half of :func:`any_event_between`: a watcher cannot be armed
    *during* the window, but it can be armed before it and outlive the thing
    that started it. This says how long before the stall the last chance to
    start one was.
    """
    index = bisect_left(events, moment)
    return events[index - 1] if index else None


def measure(projects_root: str, vault_root: str) -> Dict[str, Any]:
    """One row per distinct reset epoch, plus the hook-event gap distribution."""
    events = read_hook_events(vault_root)
    stalls, scanned = read_stalls(projects_root)

    by_reset: Dict[int, Dict[str, Any]] = {}
    for row in stalls:
        reset = int(row["resets_at"])
        bucket = by_reset.setdefault(reset, {
            "resets_at": reset,
            "limit": row["limit"],
            "sessions": [],
            "earliest_stall": None,
        })
        if row["session"] not in bucket["sessions"]:
            bucket["sessions"].append(row["session"])
        at = row["at"]
        if at is not None and (bucket["earliest_stall"] is None or at < bucket["earliest_stall"]):
            bucket["earliest_stall"] = at

    rows: List[Dict[str, Any]] = []
    for reset in sorted(by_reset):
        bucket = by_reset[reset]
        nxt = first_event_after(events, reset)
        stall_at = bucket["earliest_stall"]
        before = None if stall_at is None else last_event_before(events, stall_at)
        rows.append({
            "resets_at": reset,
            "resets_at_local": datetime.fromtimestamp(reset).strftime("%Y-%m-%d %H:%M"),
            "limit": bucket["limit"],
            "children": len(bucket["sessions"]),
            "latency_minutes": None if nxt is None else round((nxt - reset) / 60),
            "armed": None if stall_at is None else any_event_between(events, stall_at, reset),
            "hook_before_minutes": (None if (before is None or stall_at is None)
                                    else round((stall_at - before) / 60)),
            "stalled_at_local": (None if stall_at is None
                                 else datetime.fromtimestamp(stall_at).strftime("%Y-%m-%d %H:%M")),
        })

    gaps = sorted((b - a) / 60 for a, b in zip(events, events[1:]))
    return {
        "transcripts": scanned,
        "rate_limits_with_epoch": len(stalls),
        "hook_events": len(events),
        "gap_minutes": {
            "median": round(gaps[len(gaps) // 2]) if gaps else None,
            "p90": round(gaps[int(len(gaps) * 0.9)]) if gaps else None,
            "over_60": sum(1 for g in gaps if g > 60),
        },
        "resets": rows,
    }


def render(report: Dict[str, Any]) -> str:
    lines = [
        f"transcripts scanned: {report['transcripts']}",
        f"rate limits carrying a reset epoch: {report['rate_limits_with_epoch']}",
        f"mnemo hook events on record: {report['hook_events']} "
        f"(gap median {report['gap_minutes']['median']}m, "
        f"p90 {report['gap_minutes']['p90']}m, "
        f"{report['gap_minutes']['over_60']} gaps over an hour)",
        "",
        f"{'reset':18} {'limit':10} {'kids':>4} {'late(min)':>9} {'armed':>6} "
        f"{'hook-before':>11}  stalled at",
    ]
    for row in report["resets"]:
        late = "—" if row["latency_minutes"] is None else str(row["latency_minutes"])
        armed = "—" if row["armed"] is None else ("yes" if row["armed"] else "NO")
        before = ("—" if row["hook_before_minutes"] is None
                  else f"{row['hook_before_minutes']}m ago")
        lines.append(
            f"{row['resets_at_local']:18} {(row['limit'] or '—'):10} {row['children']:>4} "
            f"{late:>9} {armed:>6} {before:>11}  {row['stalled_at_local'] or '—'}"
        )
    measured = [r["latency_minutes"] for r in report["resets"] if r["latency_minutes"] is not None]
    if measured:
        lines.append("")
        lines.append(
            f"a hook-driven pass would have woken these {min(measured)}–{max(measured)} "
            f"minutes past the reset"
        )
    blind = [r for r in report["resets"] if r["armed"] is False]
    if blind:
        lines.append(
            f"{len(blind)} of {len(report['resets'])} resets had NO hook between the stall "
            "and the reset — nothing was running to arm a timer then"
        )
        near = [r["hook_before_minutes"] for r in blind
                if r["hook_before_minutes"] is not None]
        if near:
            lines.append(
                f"  but a hook had run {min(near)}–{max(near)} minutes *before* each stall: "
                "a watcher started then would still have been alive"
            )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    parser.add_argument("--vault", default=None,
                        help="vault root; default reads mnemo's own config")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    vault = args.vault
    if vault is None:
        from mnemo.core import config as config_mod
        from mnemo.core import paths as paths_mod

        vault = str(paths_mod.vault_root(config_mod.load_config()))

    report = measure(os.path.expanduser(args.projects), os.path.expanduser(vault))
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
