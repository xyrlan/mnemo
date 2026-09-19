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

Since #105 ``list_rules_by_topic`` accepts a ``query`` that gates a BM25F
rerank, and every real caller passes one (#158). A case therefore carries the
logged ``query`` when the list-call had one, and ``run_case`` replays it, so
the harness measures the path callers actually take. Cases without a query
(all pre-#105 traffic) still run and are reported separately as the legacy
baseline — never synthesised, only observed.

Public surface:
    bootstrap_cases(log_path, pair_window_s=120) -> list[Case]
    run_case(vault_root, case, reflex_index=None) -> CaseResult
    aggregate(results, log_entries=None) -> Report
    count_log_entries(log_path) -> int
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from mnemo.core.log_utils import iter_rotated_rows
from mnemo.core.mcp.tools import list_rules_by_topic

_DEFAULT_PAIR_WINDOW_S = 120.0
_RANKS_REPORTED = (3, 5, 10)

# ≥50 log entries unlocks Phase-3 retrieval-ranking work per
# docs/specs/2026-04-15-mnemo-v0.5.x-retrieval-phased.md:437.
PHASE3_THRESHOLD = 50


class _CaseOptional(TypedDict, total=False):
    # Present only when the logged list-call passed a query (#158).
    query: str


class Case(_CaseOptional):
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
    query: str | None  # what run_case passed to retrieval
    # Why the rule landed where it did, for queried cases only (#381). None
    # on an unqueried case, and on a queried one when the reflex index is
    # missing — both mean "not measured", never "measured as zero".
    query_tokens: int | None  # distinct tokens the query reduces to
    query_tokens_matched: int | None  # of those, how many the rule indexes
    bm25_score: float | None  # the rule's BM25F score against the query


class SplitReport(TypedDict):
    cases: int
    primacy_at_5: int
    primacy_rate_at_5: float
    mrr: float


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
    # #158: a "miss" is almost never a rule that was not returned. Both lists
    # are disjoint and together cover every case outside the top 5.
    buried: list[str]  # returned, but at rank > 5
    absent: list[str]  # not in the returned list at all (rank None)
    buried_rank_max: int | None  # deepest rank among ``buried``; None if empty
    # #381: of the queried cases outside the top 5, which are a ranking
    # failure and which are not one at all. Disjoint; cases whose diagnostic
    # could not be measured are in neither.
    vocabulary_gap: list[str]  # rule shares no indexed token with the query
    outranked: list[str]  # rule is scored against the query and still loses
    log_entries: int | None  # size of the access log at measurement time
    phase3_threshold: int  # ranking-change unlock threshold (log entries)
    orphan_dropped: int  # bootstrap pairs whose expect_slug is no longer in the vault
    queried: SplitReport  # cases replayed with the logged query (#158)
    unqueried: SplitReport  # legacy cases with no query — source_count+popularity path


def _parse_ts(ts: str) -> float:
    """Parse ISO-8601 Z timestamp → epoch seconds. Returns 0.0 on failure."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def _read_log(log_path: Path) -> list[dict]:
    """Load the access log — rotated ``.1`` file first, then live — skipping
    malformed lines.

    The log rotates at 1 MiB, so a list→read pair inside ``pair_window_s``
    can straddle the two files; reading only the live one would silently
    drop that case (#140).
    """
    return list(iter_rotated_rows(log_path))


def count_log_entries(log_path: Path) -> int:
    """Count entries in the access log (rotated + live); 0 if missing.

    Counts exactly the rows :func:`_read_log` sees, so the phase-3 threshold
    footer and the bootstrap agree on what "the log" is.
    """
    return sum(1 for _ in iter_rotated_rows(log_path))


def _name_to_slug(vault_root: Path) -> dict[str, str] | None:
    """Map each live rule's display name to its slug, for the #193 cutover.

    ``hit_slugs`` recorded rule *names* until 2026-09-08 and slugs after, so
    pre-cutover pairs cannot be resolved against a slug-keyed index and were
    dropped wholesale as orphans. Rebuilding the mapping from the activation
    index recovers them.

    Only names that identify **exactly one** rule are included: a name shared
    by two rules cannot be attributed to either, and guessing would inject a
    case whose expected answer is unfounded. Such names are omitted, which
    leaves the pair to be dropped by the orphan filter as before.

    A second key is registered with ``'`` doubled to ``''``. Some logged names
    escaped apostrophes that way (9 distinct names over 37 rows on the real
    log), so the log's spelling never matches the live name and the rule reads
    as deleted. Exact names are written last so a rule whose name genuinely
    contains ``''`` — such as ``z.literal('')`` — always wins over another
    rule's escaped form.

    Returns ``None`` when the index is missing, matching
    :func:`_current_slugs_for_topic` so callers can no-op the whole filter.
    """
    from mnemo.core import rule_activation

    idx = rule_activation.load_index(vault_root)
    if idx is None or "rules" not in idx:
        return None

    names: dict[str, list[str]] = {}
    for slug, rule in idx["rules"].items():
        name = rule.get("name")
        if isinstance(name, str) and name.strip():
            names.setdefault(name, []).append(slug)

    escaped: dict[str, list[str]] = {}
    for name, slugs in names.items():
        if "'" in name:
            escaped.setdefault(name.replace("'", "''"), []).extend(slugs)
    mapping = {n: s[0] for n, s in escaped.items() if len(s) == 1}
    mapping.update({n: s[0] for n, s in names.items() if len(s) == 1})
    return mapping


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
    return_orphan_count: bool = False,
) -> list[Case] | tuple[list[Case], int]:
    """Scan access log; emit one case per list→read pair within ``pair_window_s``.

    Dedup rule: a (project, topic, expect_slug, has_query) tuple appears at
    most once — if the same pair recurs, only the earliest observation is kept.
    This keeps cases.json deterministic across bootstrap runs. A queried and an
    unqueried observation of the same triple are distinct cases: they exercise
    different ranking paths, and the queried id carries a ``?q`` suffix.

    When *vault_root* is provided, pairs whose ``expect_slug`` is no longer
    present in the current activation index for ``(project, topic)`` are
    dropped. This filters out orphan cases left behind by slug renames /
    extraction-run churn that would otherwise pollute the miss list without
    pointing to a real recall defect. Backward-compatible: callers that omit
    the kwarg get every paired case (pre-filter behaviour).

    *vault_root* also supplies the name→slug mapping that recovers pre-cutover
    rows, whose ``hit_slugs`` hold rule names rather than slugs (#193). A name
    identifying exactly one live rule is rewritten to its slug; a name matching
    none, or more than one, is left alone and duly dropped by the orphan filter.
    Recovery never invents a case — it only restores pairs whose target is
    still unambiguously identifiable.

    When ``return_orphan_count=True``, returns ``(cases, dropped)`` where
    *dropped* is the number of pairs filtered as orphans in this single pass.
    Default False preserves the prior list-only shape for existing callers.
    """
    entries = _read_log(log_path)
    # Name→slug mapping for pre-cutover rows; None without a vault to read it
    # from, in which case names pass through untouched as before.
    name_map = _name_to_slug(vault_root) if vault_root is not None else None
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
            "query": (e.get("args") or {}).get("query") or None,
            "slugs": e.get("hit_slugs") or [],
        })

    seen: set[tuple[str, str, str, bool]] = set()
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
        query = best.get("query")
        # Rank comes from the raw logged list, before any rewrite — the
        # position is a property of that historical call.
        rank = best["slugs"].index(slug) + 1
        # Pre-cutover rows name the rule instead of identifying it by slug
        # (#193). Resolve to the slug here, ahead of the dedup, so the same
        # rule observed on both sides of the cutover collapses to one case
        # rather than inflating the set with a duplicate.
        expect_slug = name_map.get(slug, slug) if name_map else slug
        key = (project, best["topic"], expect_slug, query is not None)
        if key in seen:
            continue
        seen.add(key)
        case: Case = {
            "id": f"{project}:{best['topic']}:{expect_slug}" + ("?q" if query else ""),
            "project": project,
            "topic": best["topic"],
            "expect_slug": expect_slug,
            "rank_at_bootstrap": rank,
        }
        if query:
            case["query"] = query
        cases.append(case)
    cases.sort(key=lambda c: c["id"])
    dropped_count: int = 0
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
            else:
                dropped_count += 1
        cases = filtered
    if return_orphan_count:
        return cases, dropped_count
    return cases


_UNMEASURED: dict = {
    "query_tokens": None,
    "query_tokens_matched": None,
    "bm25_score": None,
}


def _vocabulary_diagnostic(
    vault_root: Path, case: Case, query: str, reflex_index: dict | None
) -> dict:
    """How much of *query* the expected rule actually indexes, and its score.

    A rank alone cannot tell a ranking failure from a vocabulary one, and the
    two want opposite fixes: re-ranking can only reorder rules the query
    scores, and a rule sharing no token with the query scores exactly zero no
    matter what any weight is set to. Measured on the 38 queried cases the log
    held on 2026-09-19, the rules inside the top 5 matched 27% of their query's
    tokens and those outside it 5%, with six of twelve matching none at all —
    so most of the miss list was never a ranking problem (#381, #158).

    Returns every key as ``None`` when the reflex index is missing: that is the
    same corpus retrieval itself falls back from, and reporting a coverage of
    zero for it would invent a vocabulary gap out of an absent index.
    """
    from mnemo.core.reflex import bm25
    from mnemo.core.reflex import index as reflex_index_mod
    from mnemo.core.reflex.tokenizer import tokenize_query

    idx = reflex_index
    if idx is None:
        idx = reflex_index_mod.load_index(vault_root)
    if idx is None:
        return dict(_UNMEASURED)
    tokens = list(dict.fromkeys(tokenize_query(query)))
    if not tokens:
        return dict(_UNMEASURED)

    slug = case["expect_slug"]
    postings = idx.get("postings", {})
    matched = 0
    for token in tokens:
        for entry in postings.get(token, []):
            if entry["slug"] == slug:
                if sum(entry.get("tf", {}).values()):
                    matched += 1
                break

    # Score against the whole doc set, not the topic bucket: the number answers
    # "does this query reach this rule at all", which is a property of the pair
    # and not of whichever bucket the case happens to name.
    scored = dict(bm25.score_docs(idx, query_tokens=tokens, candidate_slugs=[slug]))
    return {
        "query_tokens": len(tokens),
        "query_tokens_matched": matched,
        "bm25_score": round(scored.get(slug, 0.0), 4),
    }


def run_case(
    vault_root: Path, case: Case, *, reflex_index: dict | None = None
) -> CaseResult:
    """Execute a live retrieval for the case; record rank + latency.

    *reflex_index* is a pre-loaded reflex index for the vocabulary diagnostic
    to reuse. It is only an optimisation — a caller sweeping every case would
    otherwise re-read and re-parse the same file once per case — and omitting
    it changes nothing but wall time. The diagnostic is computed after the
    timer stops either way, so it never enters ``elapsed_ms`` and cannot move
    the reported p95.
    """
    query = case.get("query") or None
    t0 = time.perf_counter()
    rules = list_rules_by_topic(
        vault_root,
        case["topic"],
        scope="project",
        project=case["project"],
        query=query,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    slugs = [r["slug"] for r in rules]
    try:
        rank: int | None = slugs.index(case["expect_slug"]) + 1
    except ValueError:
        rank = None
    diagnostic = (
        _vocabulary_diagnostic(vault_root, case, query, reflex_index)
        if query else dict(_UNMEASURED)
    )
    return {
        "id": case["id"],
        "project": case["project"],
        "topic": case["topic"],
        "expect_slug": case["expect_slug"],
        "hit": rank is not None and rank <= 10,
        "rank": rank,
        "result_count": len(rules),
        "elapsed_ms": round(elapsed_ms, 3),
        "query": query,
        **diagnostic,
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


def _split(results: list[CaseResult]) -> SplitReport:
    total = len(results)
    hits = _hits_at(results, 5)
    mrr = (
        sum(1.0 / r["rank"] for r in results if r["rank"] is not None) / total
        if total else 0.0
    )
    return {
        "cases": total,
        "primacy_at_5": hits,
        "primacy_rate_at_5": round(hits / total, 4) if total else 0.0,
        "mrr": round(mrr, 4),
    }


def aggregate(
    results: list[CaseResult],
    log_entries: int | None = None,
    *,
    orphan_dropped: int = 0,
) -> Report:
    """Roll case results into a Report (primacy-rates, MRR, p95 latency).

    ``vocabulary_gap`` and ``outranked`` split the queried cases that finished
    outside the top 5 by whether re-ranking could reach them at all; see
    :func:`_vocabulary_diagnostic`. A queried case whose diagnostic was not
    measured (no reflex index) falls in neither, so the two need not sum to
    the queried share of ``buried`` + ``absent``.

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
    buried = [r["id"] for r in results if r["rank"] is not None and r["rank"] > 5]
    absent = [r["id"] for r in results if r["rank"] is None]
    buried_ranks = [r["rank"] for r in results if r["rank"] is not None and r["rank"] > 5]
    queried = [r for r in results if r.get("query")]
    unqueried = [r for r in results if not r.get("query")]
    outside = [r for r in queried if r["rank"] is None or r["rank"] > 5]
    vocabulary_gap = [r["id"] for r in outside if r.get("query_tokens_matched") == 0]
    outranked = [r["id"] for r in outside if (r.get("query_tokens_matched") or 0) > 0]
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
        "buried": buried,
        "absent": absent,
        "buried_rank_max": max(buried_ranks) if buried_ranks else None,
        "vocabulary_gap": vocabulary_gap,
        "outranked": outranked,
        "log_entries": log_entries,
        "phase3_threshold": PHASE3_THRESHOLD,
        "orphan_dropped": orphan_dropped,
        "queried": _split(queried),
        "unqueried": _split(unqueried),
    }


def format_report(report: Report, results: list[CaseResult] | None = None) -> str:
    """Human-readable one-screen summary.

    When *results* is given, each listed miss also shows the rank the rule
    actually held and the size of the list it was returned in — so a reader
    sees "rank 34/41" and not a rule that was never found (#158).
    """
    lines = [
        f"cases              : {report['cases']}",
        f"primacy@3 / @5 /@10: {report['primacy_at_3']} / {report['primacy_at_5']} / {report['primacy_at_10']}",
        f"rate @3 / @5 / @10 : {report['primacy_rate_at_3']:.2%} / {report['primacy_rate_at_5']:.2%} / {report['primacy_rate_at_10']:.2%}",
        f"MRR                : {report['mrr']:.4f}",
        f"p95 latency        : {report['p95_latency_ms']:.2f} ms",
    ]
    buried = report.get("buried", [])
    absent = report.get("absent", [])
    rank_max = report.get("buried_rank_max")
    buried_txt = f"buried {len(buried)}"
    if buried and rank_max is not None:
        buried_txt += f" (rank 6–{rank_max})"
    lines.append(
        f"outside top-5      : {len(buried) + len(absent)} = {buried_txt} + absent {len(absent)}"
    )
    gap = report.get("vocabulary_gap", [])
    outranked = report.get("outranked", [])
    if gap or outranked:
        lines.append(
            f"  of the queried    : {len(gap)} share no token with their query "
            f"(unreachable by ranking) + {len(outranked)} scored but outranked"
        )
    where = {r["id"]: (r["rank"], r["result_count"]) for r in (results or [])}
    queried = report.get("queried")
    if queried and queried["cases"]:
        unq = report["unqueried"]
        lines.append(
            f"with query         : {queried['cases']} cases, primacy@5 "
            f"{queried['primacy_at_5']} ({queried['primacy_rate_at_5']:.2%}), MRR {queried['mrr']:.4f}"
        )
        lines.append(
            f"without query      : {unq['cases']} cases, primacy@5 "
            f"{unq['primacy_at_5']} ({unq['primacy_rate_at_5']:.2%}), MRR {unq['mrr']:.4f}"
        )
    orphan = report.get("orphan_dropped", 0)
    if orphan:
        lines.append(f"orphan cases dropped: {orphan}")
    if report["misses"]:
        lines.append(f"misses ({len(report['misses'])}, rank > 10 or absent):")
        for m in report["misses"]:
            rank, n = where.get(m, (None, None))
            suffix = f"  rank {rank}/{n}" if rank is not None else ""
            lines.append(f"  - {m}{suffix}")
    if absent:
        lines.append(f"absent ({len(absent)}):")
        for m in absent:
            lines.append(f"  - {m}")
    n = report.get("log_entries")
    if n is not None:
        t = report.get("phase3_threshold", PHASE3_THRESHOLD)
        if n >= t:
            lines.append(f"phase-3 ranking-change threshold met: {n} ≥ {t} entries")
        else:
            lines.append(f"next ranking change unlocks at ≥{t} log entries; currently {n}")
    return "\n".join(lines)
