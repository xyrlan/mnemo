"""Backfill the judge-picks ledger from the history that exists today (#619)

Usage:
    PYTHONPATH=src python3 tools/backfill_judge_picks.py [--vault DIR] [--projects DIR] [--dry-run]
    PYTHONPATH=src python3 tools/backfill_judge_picks.py --size [--vault DIR]
                                    # the ledger's size, and what a compaction would leave; writes nothing

The reflex hook appends every judged prompt's picks to
``<vault>/.mnemo/judge-picks.jsonl`` (``mnemo.core.reflex.picks``) from the
version that ships it on. Before that the picks live in two places, both of
which #613 (``tools/measure_relevance_block.py``) already reads:

- every ``reflex-log.jsonl{,.1}`` row and the ``full-body-fresh`` archive
  (``reflex-rows.jsonl``) whose judge answered: one ledger row per judged
  prompt, ``picks`` the slugs scored at or above ``--inject-at``. Rows that
  appear in both files are kept once;
- before the first of those rows, the reflex blocks Claude Code recorded in
  every transcript since the judge went live (``JUDGE_LIVE``): one row per
  block, the rules it carried, read as #616 reads them. A block there is the
  judge's picks at the ``injectAt`` in force, except where the judge failed
  and the gates' own decision went out instead, which a transcript cannot
  tell apart. A prompt the judge picked nothing for left no block, so these
  rows are picks only.

Each row's project is the one the hook would have resolved from its cwd; a
transcript whose worktree is gone resolves to the directory's own name, as
every other tool here does. Rows are marked ``backfilled``, nothing at or
after the hook's own first row is written, and the ledger records
``JUDGE_LIVE`` as the moment its history is complete from. It runs once per
vault. ``--dry-run`` writes nothing and prints what it would write.

``--size`` reads the vault's ledger, compacts a copy of it in a temp
directory as of its last row, and prints the raw bytes per row and per day,
what stays raw, the summary's bytes per day of history, and whether every
rule's total survived. Those are the numbers the steady-state size is
stated from.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mrb = _sibling("measure_relevance_block")
mpr = mrb.mpr
mrc = mrb.mrc
JUDGE_LIVE = mrb.JUDGE_LIVE
INJECT_AT = mrb.INJECT_AT


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_rows(rows: Iterable[Any], inject_at: float = INJECT_AT) -> List[Dict[str, Any]]:
    """One ledger row per reflex-log row whose judge answered, once each."""
    out = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        judge = row.get("judge") or {}
        sid = row.get("session_id")
        if judge.get("status") != "ok" or not sid or mrc.epoch(row.get("ts")) is None:
            continue
        key = (sid, row.get("ts"), row.get("prompt_hash"))
        if key in seen:
            continue
        seen.add(key)
        picked = [s for s, _, _ in mrb.log_picks([row], inject_at)]
        out.append({"ts": row["ts"], "session_id": sid, "project": row.get("project") or "",
                    "picks": picked})
    return out


def transcript_rows(lines: Iterable[str], session_id: str, since: float, until: Optional[float],
                    project_of: Callable[[str], str]) -> List[Dict[str, Any]]:
    """One ledger row per reflex block recorded in ``[since, until)``."""
    out = []
    for raw in lines:
        if mpr._REFLEX not in raw:
            continue
        found = mrb.transcript_picks([raw], session_id, since)
        if not found:
            continue
        ts = found[0][2]
        if until is not None and ts >= until:
            continue
        try:
            cwd = json.loads(raw).get("cwd") or ""
        except ValueError:
            cwd = ""
        out.append({"ts": _iso(ts), "session_id": session_id, "project": project_of(cwd),
                    "picks": [slug for slug, _, _ in found]})
    return out


def _project_resolver() -> Callable[[str], str]:
    from mnemo.core import agent

    cache: Dict[str, str] = {}

    def resolve(cwd: str) -> str:
        if cwd not in cache:
            try:
                cache[cwd] = agent.resolve_canonical_agent(cwd).name if cwd else ""
            except Exception:
                cache[cwd] = ""
        return cache[cwd]
    return resolve


def collect(vault: Path, projects: Path, inject_at: float = INJECT_AT) -> Dict[str, Any]:
    from mnemo.core.log_utils import iter_rotated_rows

    m = vault / ".mnemo"
    raw = list(iter_rotated_rows(m / "reflex-log.jsonl"))
    archived = m / "full-body-fresh" / "reflex-rows.jsonl"
    if archived.is_file():
        raw += list(iter_rotated_rows(archived))
    logged = log_rows(raw, inject_at)
    log_since = min((mrc.epoch(r["ts"]) for r in logged), default=None)
    since = mrc.epoch(JUDGE_LIVE)
    resolve = _project_resolver()
    from_transcripts: List[Dict[str, Any]] = []
    for path in sorted(projects.glob("*/*.jsonl")):
        with open(path, encoding="utf-8", errors="replace") as fh:
            from_transcripts += transcript_rows(fh, path.stem, since, log_since, resolve)
    # A session the log also saw keeps the log's project: its worktree may
    # be gone, and the cwd would resolve to the directory's own name.
    known = {r["session_id"]: r["project"] for r in logged if r["project"]}
    for r in from_transcripts:
        r["project"] = known.get(r["session_id"], r["project"])
    return {"rows": logged + from_transcripts, "log_rows": len(logged),
            "transcript_rows": len(from_transcripts), "log_since": log_since, "covers_since": since}


def size_report(vault: Path, keep_days: float = 7.0) -> Dict[str, Any]:
    """The ledger's size now, and after a compaction of a copy of it."""
    import shutil
    import tempfile

    from mnemo.core.reflex import picks

    src = vault / ".mnemo" / picks.LEDGER_NAME
    ledger = picks.load(vault)
    w = ledger.window()
    if not src.is_file() or w is None:
        return {"rows": 0}
    lines = [ln for ln in src.read_text(encoding="utf-8").splitlines() if ln.strip()]
    span = max((w[1] - (ledger.since() or w[0])) / 86400.0, 1e-9)
    out: Dict[str, Any] = {"rows": len(lines), "bytes": src.stat().st_size, "days": span}
    with tempfile.TemporaryDirectory(prefix="mnemo-judge-picks-") as tmp:
        copy = Path(tmp)
        (copy / ".mnemo").mkdir()
        shutil.copy(src, copy / ".mnemo" / picks.LEDGER_NAME)
        picks.compact(copy, now=w[1] + 1, cap=0, keep_days=keep_days)
        raw = copy / ".mnemo" / picks.LEDGER_NAME
        summary = copy / ".mnemo" / picks.SUMMARY_NAME
        data = json.loads(summary.read_text(encoding="utf-8")) if summary.is_file() else {"rules": {}}
        days = {k for p in data["rules"].values() for v in p.values() for k in v}
        out.update({"raw_after": raw.stat().st_size if raw.is_file() else 0,
                    "summary": summary.stat().st_size if summary.is_file() else 0,
                    "summary_days": len(days),
                    "totals_kept": picks.load(copy).picks_before(None, float("inf"))
                    == ledger.picks_before(None, float("inf"))})
    return out


def main(argv: Optional[List[str]] = None) -> int:
    from mnemo.core import config, paths
    from mnemo.core.reflex import picks

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="")
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--inject-at", type=float, default=INJECT_AT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--size", action="store_true", help="the ledger's size and a compaction's; writes nothing")
    args = ap.parse_args(argv)

    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(config.load_config())
    if args.size:
        r = size_report(vault)
        if not r["rows"]:
            print("no judge-picks ledger rows")
            return 0
        print("ledger: %d rows, %d bytes, %.0f bytes/row, %.1f days, %.0f bytes/day" % (
            r["rows"], r["bytes"], r["bytes"] / r["rows"], r["days"], r["bytes"] / r["days"]))
        print("compacted as of its last row: %d bytes stay raw; summary %d bytes over %d day(s), "
              "%.0f bytes/day; every rule's total kept: %s" % (
                  r["raw_after"], r["summary"], r["summary_days"],
                  r["summary"] / max(1, r["summary_days"]), r["totals_kept"]))
        return 0
    got = collect(vault, Path(args.projects), args.inject_at)
    rows = got["rows"]
    n_picks = sum(len(r["picks"]) for r in rows)
    print("from reflex-log rows: %d judged prompts (since %s); from transcripts: %d reflex blocks "
          "(since %s); %d picks" % (got["log_rows"], mrb._day(got["log_since"]), got["transcript_rows"],
                                    JUDGE_LIVE, n_picks))
    if args.dry_run:
        print("dry run: nothing written")
        return 0
    try:
        written = picks.backfill(vault, rows, covers_since=got["covers_since"])
    except picks.AlreadyBackfilled as exc:
        print("refused: %s" % exc, file=sys.stderr)
        return 1
    path = vault / ".mnemo" / picks.LEDGER_NAME
    print("wrote %d backfilled row(s) to %s (%d bytes)" % (written, path, path.stat().st_size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
