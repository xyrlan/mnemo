"""How often would the full-body reflex cut a rule to fit? (#542)

Usage:
    PYTHONPATH=src python3 tools/measure_reflex_fit.py [--days 30] [--until 2026-09-28]
        [--projects ~/.claude/projects] [--json]

Read-only: no LLM calls, no writes, no behaviour change.

#542 ships the rule's whole body in the reflex (#535 measured it at +0.236
[+0.090, +0.396] over the one-line preview) and holds the block under Claude
Code's persist limit, ``hook_envelope.ENVELOPE_MAX_BYTES`` (#533). A block
over it gives each rule a fair share and cuts the long ones. This replays
every reflex emission from the last ``--days`` through the hook's own
renderer (``mnemo.core.reflex.render``), with the rule pages as they stand
today, and counts the cuts.

**Two sources, both reported.** ``reflex-log.jsonl`` (and its rotated
``.1``) is the hook's own record, but it rotates at 1 MB and on a busy vault
holds days, not a month. The transcripts under ``--projects`` do not rotate:
every ``mnemo reflex context:`` block the hook wrote is there as a
``hook_additional_context`` attachment, and its ``• [[slug]]`` heads are the
rules it emitted. A block copied into a second transcript (a resume, a fork)
counts once: rows are keyed by session, timestamp and slugs.

**What it reports**, per source: the window it actually covers, emission rows
and emitted rules, how many rows would have had a rule cut and how many rules
were cut, rules whose page is gone from the vault (rendered as their preview,
so never cut), and the rendered block's bytes (median, p90, max).

**The result on the maintainer's vault (2026-09-28, ``--until 2026-09-28``,
30 days).** The transcripts hold 1,439 reflex emissions (2,148 rules) from
2026-08-31 to 2026-09-28: **131 rows (9.1%) would have had a rule cut, 154
rules (7.2%)**; 123 rules' pages are gone since (renamed or dropped) and were
counted as previews. The rendered block is a median 2,373 bytes (p90 8,358,
max 8,999), none over the 9,000 cap. The log covers only 2026-09-24 →
09-28 (452 rows, 653 rules): 39 rows (8.6%), 45 rules (6.9%), the same rate.
Over that shared window the two sources agree to 446 of 452 rows, in the same
175 sessions.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from mnemo.core.hook_envelope import ENVELOPE_MAX_BYTES
from mnemo.core.reflex import render

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]


def emissions(rows: Iterable[Dict[str, Any]], since: str, until: str) -> List[Dict[str, Any]]:
    """Rows that emitted at least one rule, with ``since <= ts <= until``
    (ISO strings compare in order)."""
    out = []
    for row in rows:
        ts = str(row.get("ts") or "")
        if row.get("silence_reason") is not None or not row.get("emitted"):
            continue
        if since <= ts <= until:
            out.append(row)
    return out


_HEAD = re.compile(r"^• \[\[([^\]\s|]+)\]\]", re.M)


def _texts(attachment: Dict[str, Any]) -> List[str]:
    content = attachment.get("content")
    if isinstance(content, str):
        return [content]
    return [c for c in content or [] if isinstance(c, str)]


def transcript_emissions(events: Iterable[Any]) -> List[Dict[str, Any]]:
    """The reflex blocks in one transcript, as log-shaped emission rows."""
    out = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        att = ev.get("attachment")
        if not isinstance(att, dict) or att.get("type") != "hook_additional_context":
            continue
        for text in _texts(att):
            if not text.startswith(render.HEADER):
                continue
            slugs = _HEAD.findall(text)
            if slugs:
                out.append({"ts": _iso(str(ev.get("timestamp") or "")), "emitted": slugs,
                            "session_id": ev.get("sessionId"), "silence_reason": None})
    return out


def _iso(ts: str) -> str:
    """A transcript timestamp (``…T10:00:00.123Z``) in the log's shape."""
    return ts[:19] + "Z" if len(ts) >= 19 else ts


def _events(path: Path) -> Iterable[Any]:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def transcript_rows(projects: Path) -> List[Dict[str, Any]]:
    """Every reflex emission in every transcript under ``projects``, once."""
    seen, out = set(), []
    for path in sorted(projects.rglob("*.jsonl")) if projects.is_dir() else []:
        for row in transcript_emissions(_events(path)):
            key = (row["session_id"], row["ts"], tuple(row["emitted"]))
            if key not in seen:
                seen.add(key)
                out.append(row)
    return out


def pages(vault: Path) -> Dict[str, Dict[str, Any]]:
    """slug → ``{"preview", "body", "path"}`` for every page the reflex can emit."""
    from mnemo.core.reflex.index import build_index

    out = {}
    for slug, doc in (build_index(vault).get("docs") or {}).items():
        page = vault / doc["path"]
        try:
            body = render.full_body(page.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            body = None
        out[slug] = {"preview": doc.get("preview", ""), "body": body or None, "path": str(page)}
    return out


def _quantiles(values: List[int]) -> Dict[str, Any]:
    if not values:
        return {"median": None, "p90": None, "max": None}
    s = sorted(values)
    return {"median": int(statistics.median(s)), "p90": s[min(len(s) - 1, int(0.9 * len(s)))],
            "max": s[-1]}


def replay(rows: List[Dict[str, Any]], known: Dict[str, Dict[str, Any]],
           max_bytes: int = ENVELOPE_MAX_BYTES) -> Dict[str, Any]:
    """Render every row's emitted rules in the ``full`` format and count cuts."""
    rules = cut = missing = rows_cut = 0
    sizes: List[int] = []
    for row in rows:
        entries = []
        for slug in row["emitted"]:
            page = known.get(slug)
            if page is None:
                missing += 1
                entries.append(render.Entry(slug=slug, preview=""))
            else:
                entries.append(render.Entry(slug=slug, preview=page["preview"],
                                            body=page["body"], path=page["path"]))
        out = render.render(entries, render.FULL, max_bytes)
        present = [w for e, w in zip(entries, out.rule_whole) if e.body is not None]
        rules += len(entries)
        n_cut = sum(1 for w in present if not w)
        cut += n_cut
        rows_cut += 1 if n_cut else 0
        sizes.append(len(out.text.encode("utf-8")))
    ts = sorted(str(r.get("ts") or "") for r in rows)
    return {"rows": len(rows), "rules": rules, "rows_cut": rows_cut, "rules_cut": cut,
            "rules_missing": missing, "first": ts[0] if ts else None,
            "last": ts[-1] if ts else None, "block_bytes": _quantiles(sizes),
            "over_cap": sum(1 for s in sizes if s > max_bytes), "max_bytes": max_bytes}


def _pct(n: int, d: int) -> str:
    return "%.1f%%" % (100.0 * n / d) if d else "—"


def report_lines(data: Dict[str, Any]) -> List[str]:
    lines = ["window asked      %s → %s" % (data["since"], data["until"])]
    for name in ("log", "transcripts"):
        lines += ["", "[%s]" % name] + _source_lines(data[name])
    return lines


def _source_lines(d: Dict[str, Any]) -> List[str]:
    q = d["block_bytes"]
    return [
        "covers            %s → %s" % (d["first"], d["last"]),
        "emission rows     %d   rules emitted %d" % (d["rows"], d["rules"]),
        "rows with a cut   %d   %s" % (d["rows_cut"], _pct(d["rows_cut"], d["rows"])),
        "rules cut         %d   %s of rules" % (d["rules_cut"], _pct(d["rules_cut"], d["rules"])),
        "page gone         %d   (sent as their preview, never cut)" % d["rules_missing"],
        "block bytes       median %s   p90 %s   max %s   (cap %d, over it %d)" % (
            q["median"], q["p90"], q["max"], d["max_bytes"], d["over_cap"]),
    ]


def run(vault: Path, days: int, until: Optional[str] = None,
        projects: Optional[Path] = None) -> Dict[str, Any]:
    from mnemo.core.log_utils import iter_rotated_rows

    end = (datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
           if until else datetime.now(timezone.utc))
    since = (end - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stop = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    known = pages(vault)
    log = emissions(iter_rotated_rows(vault / ".mnemo" / "reflex-log.jsonl"), since, stop)
    seen = emissions(transcript_rows(projects), since, stop) if projects else []
    return {"since": since, "until": stop,
            "log": replay(log, known), "transcripts": replay(seen, known)}


def main(argv: Optional[List[str]] = None) -> int:
    from mnemo.core import config as cfg_mod
    from mnemo.core import paths

    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--until", default="", help="ISO date, inclusive (default: now)")
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    vault = paths.vault_root(cfg_mod.load_config())
    d = run(vault, args.days, args.until or None, Path(args.projects))
    prov = _provenance.provenance(__file__, argv, vault=vault,
                                  blind_spots=[_provenance.transcripts_blind_spot(args.projects)])
    if args.json:
        json.dump(_provenance.stamp(d, prov), sys.stdout, indent=2)
        print()
    else:
        print(_provenance.line(prov))
        print("\n".join(report_lines(d)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
