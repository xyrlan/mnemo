"""What #762's three changes would do to an inbox like the one it found.

Usage:
    PYTHONPATH=src python3 tools/measure_inbox_backlog.py --vault ~/scratch-copy
    PYTHONPATH=src python3 tools/measure_inbox_backlog.py --vault V \\
        --projects ~/.claude/projects [--now 2026-10-28T12:00:00] [--json]

**Read only**: it calls ``inbox.staged_pages``, ``inbox.expires_at`` and the
ledger readers — the sweep's and the offer's own functions — and never
``expire_held`` or ``pick_offers``, which write.

Three dry runs:

* **expiry** — every staged page (``.proposed.md`` rewrites excluded, as the
  queue excludes them) by reason, and how many are due *now* under the old
  rule (``demotionExpiryDays`` = 0: judge-held and backfill only) and the new
  one (the config's windows), with the newly expiring demotions split by the
  judge's verdict;
* **offer** — the ledger's blocks (``offered`` rows sharing a project and a
  minute) replayed under the backoff: a block is kept unless the queue was
  idle then (:func:`inbox._idle` over the real ledger's rows up to that
  moment) and a kept block of any project went out inside
  ``offerIdleIntervalHours``. A floor on what the backoff saves rather than
  an exact count: a session the backoff silenced is not a session the old
  rule recorded, so the replay cannot see the later session that would have
  carried the block instead;
* **sessions** (with ``--projects``) — the transcripts whose session start
  carried a staged block, split by whether the cwd was a dispatch worktree,
  where the block no longer goes.

No project names are printed: they can be private.

First run, 2026-10-28 10:45, the maintainer's Windows vault (read only) and
its transcripts: 514 staged — 344 demotion, 120 other, 50 multi-source.
Due now under the old rule: 13 (12 other, 1 demotion — judge-held or
backfill pages the next extraction's sweep has not reached yet). Under the
new one: 250, the 13 plus 237 demotions — 178 the judge rated system, 57
technique, 2 unjudged. Ledger 2026-10-13..28: 36 blocks, 0 human decisions;
the backoff keeps 14 of them. Transcripts: 26 sessions carried a block, 7 of
them in a dispatch worktree.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from mnemo.core import inbox as I

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

#: What a session start's staged block begins with.
BLOCK_MARK = "[mnemo staged for review — project="
#: Its ASCII head, to skip a transcript without parsing it: a writer that
#: escapes non-ASCII spells the dash ``\u2014``, and that must still match.
_PREFILTER = "[mnemo staged for review"


def expiry(vault: Path, cfg: dict, now: datetime) -> dict:
    """Staged pages by reason, and how many each rule would archive now."""
    old_cfg = {"inbox": dict((cfg or {}).get("inbox") or {}, demotionExpiryDays=0)}
    restored = I.restored_keys(vault)
    pages = I.staged_pages(vault)
    old_due: Counter = Counter()
    new_due: Counter = Counter()
    new_demotions: Counter = Counter()
    for page in pages:
        old = I.expires_at(page, old_cfg, restored=restored)
        new = I.expires_at(page, cfg, restored=restored)
        old_hit = old is not None and old <= now
        new_hit = new is not None and new <= now
        if old_hit:
            old_due[page.reason] += 1
        if new_hit:
            new_due[page.reason] += 1
        if new_hit and not old_hit:
            new_demotions[page.gate_verdict or "unjudged"] += 1
    return {
        "demotion_days": I.demotion_expiry_days(cfg),
        "held_days": I.held_expiry_days(cfg),
        "staged": len(pages),
        "by_reason": dict(Counter(p.reason for p in pages).most_common()),
        "due_old": dict(old_due),
        "due_new": dict(new_due),
        "newly_due_by_verdict": dict(new_demotions.most_common()),
    }


def blocks(rows: List[dict]) -> List[tuple]:
    """``(ts, project)`` per block the ledger records, oldest first."""
    seen: Dict[tuple, datetime] = {}
    for row in rows:
        if row.get("event") != I.OFFERED:
            continue
        ts = I._parse_ts(row.get("ts"))
        if ts is None:
            continue
        key = (ts.replace(second=0, microsecond=0), str(row.get("project") or ""))
        seen.setdefault(key, ts)
    return sorted((ts, project) for (_, project), ts in seen.items())


def offer(vault: Path, cfg: dict) -> dict:
    """The ledger's blocks, and how many the backoff would have let through."""
    settings = I.offer_settings(cfg)
    rows = I.read_ledger(vault)
    found = blocks(rows)
    human = [r for r in rows if r.get("event") in I._HUMAN_EVENTS]
    kept: List[datetime] = []
    for ts, _project in found:
        # What the hook would have read: the decisions before this moment and
        # the blocks the backoff itself let out, not the ones it silenced.
        sim = [r for r in human if (I._parse_ts(r.get("ts")) or ts) < ts]
        sim += [{"ts": k.isoformat(), "event": I.OFFERED, "key": "-"} for k in kept]
        idle = I._idle(sim, idle_days=settings["idle_days"], ref=ts)
        window = timedelta(hours=settings["idle_interval_hours"])
        if idle and window and kept and ts - kept[-1] < window:
            continue
        kept.append(ts)
    first = found[0][0].date().isoformat() if found else None
    last = found[-1][0].date().isoformat() if found else None
    return {
        "idle_days": settings["idle_days"],
        "idle_interval_hours": settings["idle_interval_hours"],
        "since": first,
        "until": last,
        "blocks": len(found),
        "human_decisions": len(human),
        "kept_under_backoff": len(kept),
    }


def _is_dispatch(cwd: str) -> bool:
    from mnemo.core.dispatch import issue_for_cwd

    return issue_for_cwd(cwd) is not None


def sessions(projects: Path) -> dict:
    """Transcripts whose session start carried a staged block, by kind of cwd."""
    out = Counter()
    for path in sorted(Path(projects).rglob("*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _PREFILTER not in text:
            continue
        for line in text.splitlines():
            if _PREFILTER not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            # The hook's injection, not a later quote of it in a prompt or a
            # tool result (an issue body, a grep of this file).
            if row.get("type") != "attachment":
                continue
            if BLOCK_MARK not in json.dumps(row, ensure_ascii=False):
                continue
            out["dispatch" if _is_dispatch(str(row.get("cwd") or "")) else "interactive"] += 1
            break
    return {"interactive": out["interactive"], "dispatch": out["dispatch"]}


def measure(vault: Path, cfg: Optional[dict] = None, *, now: Optional[datetime] = None,
            projects: Optional[Path] = None) -> dict:
    cfg = cfg or {}
    now = now or datetime.now()
    report = {
        "now": now.isoformat(timespec="seconds"),
        "expiry": expiry(vault, cfg, now),
        "offer": offer(vault, cfg),
    }
    if projects is not None:
        report["sessions"] = sessions(projects)
    return report


def render(report: dict) -> str:
    e, o = report["expiry"], report["offer"]
    lines = [
        f"now {report['now']}",
        f"staged {e['staged']}  {e['by_reason']}",
        f"due now, old rule (held/backfill {e['held_days']}d): {sum(e['due_old'].values())} "
        f"{e['due_old']}",
        f"due now, new rule (+ demotions {e['demotion_days']}d): {sum(e['due_new'].values())} "
        f"{e['due_new']}",
        f"  newly due, by judge verdict: {e['newly_due_by_verdict']}",
        f"ledger {o['since']}..{o['until']}: {o['blocks']} blocks, "
        f"{o['human_decisions']} human decisions",
        f"  kept under the backoff ({o['idle_days']}d idle -> one per "
        f"{o['idle_interval_hours']}h vault-wide): {o['kept_under_backoff']}",
    ]
    if "sessions" in report:
        s = report["sessions"]
        lines.append(f"sessions that carried a block: {s['interactive'] + s['dispatch']}, "
                     f"{s['dispatch']} in a dispatch worktree (now none)")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", required=True, type=Path)
    ap.add_argument("--projects", type=Path, default=None,
                    help="Claude Code transcripts dir, e.g. ~/.claude/projects")
    ap.add_argument("--now", default=None, help="ISO time to measure at (default: now)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    from mnemo.core import config as cfg_mod

    vault = args.vault.expanduser()
    now = datetime.fromisoformat(args.now) if args.now else None
    projects = args.projects.expanduser() if args.projects else None
    report = measure(vault, cfg_mod.load_config(), now=now, projects=projects)
    prov = _provenance.provenance(
        __file__, argv, vault=vault,
        blind_spots=[_provenance.transcripts_blind_spot(projects)] if projects else (),
    )
    if args.json:
        print(json.dumps(_provenance.stamp(report, prov), indent=2))
    else:
        print(_provenance.line(prov))
        print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
