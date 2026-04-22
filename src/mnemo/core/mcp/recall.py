"""Retrieval regression harness — measures mnemo ranking without dogfood.

The harness converts historical ``mcp-access-log.jsonl`` entries into
regression cases by pairing each ``list_rules_by_topic`` call with the
``read_mnemo_rule`` call that consumed one of its returned slugs. A consumed
slug is a weak but honest relevance signal: at the time of the call, that rule
was what the caller actually opened.

``primacy@N`` is the chosen name for the hit-rate metric: the MCP retrieval
path does NOT truncate results (``src/mnemo/core/mcp/tools.py``
``list_rules_by_topic`` returns the full list, and
``src/mnemo/core/mcp/server.py`` passes it through unchanged). The metric
therefore measures how often the expected slug appears in the first N positions
Claude scans — i.e. primacy-bias exposure — not visibility. Mislabelling this as
"hit rate" prompted a flawed follow-up proposal; the rename guards against the
same mistake.

Public surface:
    bootstrap_cases(log_path, pair_window_s=120) -> list[Case]
    run_case(vault_root, case) -> CaseResult
    aggregate(results, log_entries=None) -> Report
    count_log_entries(log_path) -> int
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from mnemo.core.mcp.tools import list_rules_by_topic

_DEFAULT_PAIR_WINDOW_S = 120.0
_RANKS_REPORTED = (3, 5, 10)

# ≥50 log entries unlocks Phase-3 retrieval-ranking work per
# docs/specs/2026-04-15-mnemo-v0.5.x-retrieval-phased.md:437.
PHASE3_THRESHOLD = 50


class Case(TypedDict):
    id: str
    project: str
    topic: str
    expect_slug: str
    # Historical rank (1-indexed) at bootstrap time — for drift comparison.
    rank_at_bootstrap: int


class CaseResult(TypedDict):
    id: str
    project: str
    topic: str
    expect_slug: str
    hit: bool
    rank: int | None  # None = slug not in returned list
    result_count: int
    elapsed_ms: float


class Report(TypedDict):
    cases: int
    primacy_at_3: int
    primacy_at_5: int
    primacy_at_10: int
    primacy_rate_at_3: float
    primacy_rate_at_5: float
    primacy_rate_at_10: float
    mrr: float
    p95_latency_ms: float
    misses: list[str]  # case ids with rank > 10 (or absent)
    log_entries: int | None  # size of the access log at measurement time
    phase3_threshold: int  # ranking-change unlock threshold (log entries)
    orphan_dropped: int  # bootstrap pairs whose expect_slug is no longer in the vault


def _parse_ts(ts: str) -> float:
    """Parse ISO-8601 Z timestamp → epoch seconds. Returns 0.0 on failure."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def _read_log(log_path: Path) -> list[dict]:
    """Load JSONL access log, skipping malformed lines."""
    if not log_path.is_file():
        return []
    entries: list[dict] = []
    for raw in log_path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return entries


def count_log_entries(log_path: Path) -> int:
    """Count non-blank lines in the access log; 0 if missing."""
    if not log_path.is_file():
        return 0
    return sum(
        1 for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()
    )


def _current_slugs_for_topic(
    vault_root: Path, project: str, topic: str
) -> set[str] | None:
    """Return the set of slugs currently indexed for (project, topic).

    Union of project-local slugs and universal slugs whose ``topic_tags``
    include *topic*. Used to decide whether a historical bootstrap pair still
    points at a real rule in the current vault.

    Returns ``None`` (not an empty set) when the activation index is missing
    so callers can distinguish "index unavailable → filter no-op" from
    "index present but no rules tagged this topic → every pair is orphan".
    """
    from mnemo.core import rule_activation

    idx = rule_activation.load_index(vault_root)
    if idx is None or "rules" not in idx:
        return None
    rules = idx["rules"]
    by_project = idx.get("by_project", {}).get(project, {})
    local_slugs = set(by_project.get("local_slugs", []))
    universal_slugs = set(idx.get("universal", {}).get("slugs", []))
    candidates = local_slugs | universal_slugs
    return {
        slug for slug in candidates
        if topic in rules.get(slug, {}).get("topic_tags", [])
    }


def bootstrap_cases(
    log_path: Path,
    pair_window_s: float = _DEFAULT_PAIR_WINDOW_S,
    *,
    vault_root: Path | None = None,
) -> list[Case]:
    """Scan access log; emit one case per list→read pair within ``pair_window_s``.

    Dedup rule: a (project, topic, expect_slug) triple appears at most once — if
    the same pair recurs, only the earliest observation is kept. This keeps
    cases.json deterministic across bootstrap runs.

    When *vault_root* is provided, pairs whose ``expect_slug`` is no longer
    present in the current activation index for ``(project, topic)`` are
    dropped. This filters out orphan cases left behind by slug renames /
    extraction-run churn that would otherwise pollute the miss list without
    pointing to a real recall defect. Backward-compatible: callers that omit
    the kwarg get every paired case (pre-filter behaviour).
    """
    entries = _read_log(log_path)
    # Index list-calls by project for fast lookup.
    lists_by_project: dict[str, list[dict]] = {}
    for e in entries:
        if e.get("tool") != "list_rules_by_topic":
            continue
        project = e.get("project")
        if not project:
            continue
        lists_by_project.setdefault(project, []).append({
            "ts": _parse_ts(e.get("timestamp", "")),
            "topic": (e.get("args") or {}).get("topic"),
            "slugs": e.get("hit_slugs") or [],
        })

    seen: set[tuple[str, str, str]] = set()
    cases: list[Case] = []
    for e in entries:
        if e.get("tool") != "read_mnemo_rule":
            continue
        project = e.get("project")
        slug = (e.get("args") or {}).get("slug")
        if not project or not slug:
            continue
        read_ts = _parse_ts(e.get("timestamp", ""))
        if read_ts == 0.0:
            continue
        # Find the most recent list-call in this project, within window,
        # that returned the read slug.
        best: dict | None = None
        for lc in lists_by_project.get(project, []):
            if lc["ts"] > read_ts:
                continue
            if (read_ts - lc["ts"]) > pair_window_s:
                continue
            if slug not in lc["slugs"]:
                continue
            if best is None or lc["ts"] > best["ts"]:
                best = lc
        if best is None or not best.get("topic"):
            continue
        key = (project, best["topic"], slug)
        if key in seen:
            continue
        seen.add(key)
        rank = best["slugs"].index(slug) + 1
        cases.append({
            "id": f"{project}:{best['topic']}:{slug}",
            "project": project,
            "topic": best["topic"],
            "expect_slug": slug,
            "rank_at_bootstrap": rank,
        })
    cases.sort(key=lambda c: c["id"])
    if vault_root is not None:
        # Cache current-vault slug sets per (project, topic) to avoid rebuilding
        # the set for each case when the same topic recurs.
        topic_cache: dict[tuple[str, str], set[str] | None] = {}
        filtered: list[Case] = []
        for c in cases:
            key = (c["project"], c["topic"])
            if key not in topic_cache:
                topic_cache[key] = _current_slugs_for_topic(vault_root, *key)
            slugs = topic_cache[key]
            # ``None`` means the activation index is missing → filter is a
            # no-op (keep every case). A non-None set (even empty) means the
            # index is authoritative for this (project, topic) and any
            # expect_slug not in it is orphan.
            if slugs is None or c["expect_slug"] in slugs:
                filtered.append(c)
        cases = filtered
    return cases


def run_case(vault_root: Path, case: Case) -> CaseResult:
    """Execute a live retrieval for the case; record rank + latency."""
    t0 = time.perf_counter()
    rules = list_rules_by_topic(
        vault_root,
        case["topic"],
        scope="project",
        project=case["project"],
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    slugs = [r["slug"] for r in rules]
    try:
        rank: int | None = slugs.index(case["expect_slug"]) + 1
    except ValueError:
        rank = None
    return {
        "id": case["id"],
        "project": case["project"],
        "topic": case["topic"],
        "expect_slug": case["expect_slug"],
        "hit": rank is not None and rank <= 10,
        "rank": rank,
        "result_count": len(rules),
        "elapsed_ms": round(elapsed_ms, 3),
    }


def _hits_at(results: list[CaseResult], n: int) -> int:
    return sum(1 for r in results if r["rank"] is not None and r["rank"] <= n)


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile. ``pct`` in [0, 100]. Returns 0.0 on empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round((pct / 100) * len(ordered))) - 1))
    return ordered[k]


def aggregate(
    results: list[CaseResult],
    log_entries: int | None = None,
    *,
    orphan_dropped: int = 0,
) -> Report:
    """Roll case results into a Report (primacy-rates, MRR, p95 latency).

    ``log_entries``, when provided, is stored alongside the threshold constant
    so consumers can display unlock progress. ``orphan_dropped`` is the count
    of bootstrap pairs filtered out because their ``expect_slug`` no longer
    exists in the current activation index; surfaced so operators can see
    filter activity vs. the raw log. See module docstring for why the metric
    is called "primacy" and not "hit rate".
    """
    total = len(results)
    hits = {n: _hits_at(results, n) for n in _RANKS_REPORTED}
    mrr = (
        sum(1.0 / r["rank"] for r in results if r["rank"] is not None) / total
        if total else 0.0
    )
    p95 = _percentile([r["elapsed_ms"] for r in results], 95.0)
    misses = [r["id"] for r in results if r["rank"] is None or r["rank"] > 10]
    return {
        "cases": total,
        "primacy_at_3": hits[3],
        "primacy_at_5": hits[5],
        "primacy_at_10": hits[10],
        "primacy_rate_at_3": round(hits[3] / total, 4) if total else 0.0,
        "primacy_rate_at_5": round(hits[5] / total, 4) if total else 0.0,
        "primacy_rate_at_10": round(hits[10] / total, 4) if total else 0.0,
        "mrr": round(mrr, 4),
        "p95_latency_ms": round(p95, 3),
        "misses": misses,
        "log_entries": log_entries,
        "phase3_threshold": PHASE3_THRESHOLD,
        "orphan_dropped": orphan_dropped,
    }


def format_report(report: Report) -> str:
    """Human-readable one-screen summary."""
    lines = [
        f"cases              : {report['cases']}",
        f"primacy@3 / @5 /@10: {report['primacy_at_3']} / {report['primacy_at_5']} / {report['primacy_at_10']}",
        f"rate @3 / @5 / @10 : {report['primacy_rate_at_3']:.2%} / {report['primacy_rate_at_5']:.2%} / {report['primacy_rate_at_10']:.2%}",
        f"MRR                : {report['mrr']:.4f}",
        f"p95 latency        : {report['p95_latency_ms']:.2f} ms",
    ]
    orphan = report.get("orphan_dropped", 0)
    if orphan:
        lines.append(f"orphan cases dropped: {orphan}")
    if report["misses"]:
        lines.append(f"misses ({len(report['misses'])}):")
        for m in report["misses"]:
            lines.append(f"  - {m}")
    n = report.get("log_entries")
    if n is not None:
        t = report.get("phase3_threshold", PHASE3_THRESHOLD)
        if n >= t:
            lines.append(f"phase-3 ranking-change threshold met: {n} ≥ {t} entries")
        else:
            lines.append(f"next ranking change unlocks at ≥{t} log entries; currently {n}")
    return "\n".join(lines)
