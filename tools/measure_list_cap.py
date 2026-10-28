"""What a cap on ``list_rules_by_topic`` costs and saves (#765).

Usage:
    PYTHONPATH=src python3 tools/measure_list_cap.py                # the access log, local
    PYTHONPATH=src python3 tools/measure_list_cap.py --days 14      # the last 14 days of it
    PYTHONPATH=src python3 tools/measure_list_cap.py --limit 5 --limit 10 --json

Nothing here leaves the machine: it reads ``.mnemo/mcp-access-log.jsonl`` and
prints counts.

For every logged ``list_rules_by_topic`` row it asks what the server's own
:func:`mnemo.core.mcp.server.cap` would have shown at each ``limit`` — the
first ``limit`` slugs, stretched to every slug the rerank marked — and
reports, per limit:

- **bytes**: the JSON of the shown slugs, p50 / p90 / max, against the slugs
  the row actually listed;
- **reads outside**: of the ``read_mnemo_rule`` calls that followed a list in
  the same session, how many read a slug no earlier list of that session
  would have shown. Those reads are what the cap moves behind ``limit=0``;
  whether they were worth making the log cannot say.

A read is paired with every earlier list of its session (the session id is on
both rows since #416); a read no list showed is off-list and not counted.

It also splits ``elapsed_ms`` by the rerank stage's status: a call that never
reached the provider (``no_key``, ``small``, stage off) is the local cost —
index loads, scope, BM25F — and ``ok`` adds the provider's round trip.

A row written after #765 already lists only the capped head (it carries
``total``), so it says nothing about a smaller cap than the one it ran under;
such rows are counted and left out of the cap columns.

First run, 2026-10-28, a Windows vault's two weeks (63 lists, 141 paired
reads): the full lists were p50 2,937 / max 10,157 bytes of slugs; limit 10
gives p50 453 / max 1,645 and leaves 30 of the 141 reads outside — all of
them rules the judge had not marked. ``no_key`` calls took p50 1.1 s, ``ok``
p50 4.2 s.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from mnemo.core.mcp.server import DEFAULT_LIST_LIMIT, cap

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

LIST = "list_rules_by_topic"
READ = "read_mnemo_rule"


def read_rows(path: Path) -> List[Dict[str, Any]]:
    """Every JSON object in the log; a torn or foreign line is skipped."""
    out: List[Dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def since(rows: Iterable[Dict[str, Any]], cutoff: str) -> List[Dict[str, Any]]:
    return [r for r in rows if str(r.get("timestamp") or "") >= cutoff]


def shown(row: Dict[str, Any], limit: int) -> List[str]:
    """The slugs ``cap`` would have shown of this row's list."""
    marked = set((row.get("rerank") or {}).get("relevant_slugs") or [])
    rules = [{"slug": s, "relevant": s in marked} for s in row.get("hit_slugs") or []]
    return [r["slug"] for r in cap(rules, limit)]


def _quantiles(values: Sequence[float]) -> Dict[str, Any]:
    v = sorted(values)
    if not v:
        return {"n": 0, "p50": None, "p90": None, "max": None}
    return {"n": len(v), "p50": v[len(v) // 2], "p90": v[min(len(v) - 1, int(len(v) * 0.9))],
            "max": v[-1]}


def report(rows: Sequence[Dict[str, Any]], limits: Sequence[int]) -> Dict[str, Any]:
    lists = [r for r in rows if r.get("tool") == LIST]
    full = [r for r in lists if "total" not in r]
    by_session: Dict[Any, List[Dict[str, Any]]] = {}
    reads: List[tuple] = []
    for row in rows:
        sid = row.get("session_id")
        if row.get("tool") == LIST and "total" not in row:
            by_session.setdefault(sid, []).append(row)
        elif row.get("tool") == READ and sid is not None:
            slug = (row.get("args") or {}).get("slug")
            earlier = [x for x in by_session.get(sid, []) if slug in (x.get("hit_slugs") or [])]
            if earlier:
                reads.append((slug, earlier))

    def size(slugs: List[str]) -> int:
        return len(json.dumps(slugs))

    caps = []
    for limit in limits:
        caps.append({
            "limit": limit,
            "bytes": _quantiles([size(shown(r, limit)) for r in full]),
            "items": _quantiles([len(shown(r, limit)) for r in full]),
            "reads_outside": sum(1 for slug, earlier in reads
                                 if not any(slug in shown(x, limit) for x in earlier)),
        })
    latency: Dict[str, List[float]] = {}
    for r in lists:
        status = (r.get("rerank") or {}).get("status") or "off"
        if isinstance(r.get("elapsed_ms"), (int, float)):
            latency.setdefault(status, []).append(float(r["elapsed_ms"]))
    return {
        "lists": len(lists),
        "already_capped": len(lists) - len(full),
        "paired_reads": len(reads),
        "listed": {"bytes": _quantiles([size(r.get("hit_slugs") or []) for r in full]),
                   "items": _quantiles([len(r.get("hit_slugs") or []) for r in full])},
        "caps": caps,
        "elapsed_ms_by_rerank_status": {k: _quantiles(v) for k, v in sorted(latency.items())},
    }


def _q(d: Dict[str, Any], unit: str = "") -> str:
    if not d.get("n"):
        return "-"
    return "p50 %s%s / p90 %s%s / max %s%s" % (
        round(d["p50"]), unit, round(d["p90"]), unit, round(d["max"]), unit)


def format_report(data: Dict[str, Any]) -> str:
    lines = ["list_rules_by_topic: %d lists (%d already capped, left out), %d paired reads"
             % (data["lists"], data["already_capped"], data["paired_reads"]),
             "  as listed: %s bytes, items %s" % (_q(data["listed"]["bytes"]), _q(data["listed"]["items"]))]
    for c in data["caps"]:
        lines.append("  limit %-3d %s bytes, items %s, reads outside %d of %d" % (
            c["limit"], _q(c["bytes"]), _q(c["items"]), c["reads_outside"], data["paired_reads"]))
    lines.append("elapsed_ms by rerank status:")
    for status, q in data["elapsed_ms_by_rerank_status"].items():
        lines.append("  %-8s n %-3d %s" % (status, q["n"], _q(q, " ms")))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vault", type=Path, default=None, help="vault root (default: the configured one)")
    parser.add_argument("--days", type=int, default=0, help="only the last N days (default: all)")
    parser.add_argument("--limit", type=int, action="append", default=None,
                        help="a cap to evaluate; repeatable (default: 5, 10, 15, 20)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    vault = args.vault
    if vault is None:
        from mnemo.core.config import load_config
        from mnemo.core.paths import vault_root
        vault = vault_root(load_config())
    rows = read_rows(Path(vault) / ".mnemo" / "mcp-access-log.jsonl")
    if args.days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = since(rows, cutoff)
    limits = args.limit or sorted({5, DEFAULT_LIST_LIMIT, 15, 20})
    data = report(rows, limits)
    prov = _provenance.provenance(__file__, argv, vault=vault)
    if args.json:
        print(json.dumps(_provenance.stamp(data, prov), indent=2))
    else:
        print(format_report(data))
        print(_provenance.line(prov))
    return 0


if __name__ == "__main__":
    sys.exit(main())
