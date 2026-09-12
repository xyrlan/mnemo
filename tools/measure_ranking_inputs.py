"""Measure the two ranking inputs #158 asks about, against the real access log.

Usage:
    PYTHONPATH=src python3 tools/measure_ranking_inputs.py [--vault ~/mnemo] [--json]

Read-only: no LLM calls, no writes, no ranking change. Answers the two
observations #158 records as "check before designing anything":

1. How many logged ``list_rules_by_topic`` calls actually pass a ``query``.
   This decides whether the recall harness — which before #162 never sent one —
   was measuring a code path real callers take.

2. How often ``source_count`` (the primary sort key) fails to discriminate,
   measured as the share of within-bucket rule pairs that tie on it, and again
   after the popularity tiebreak is applied.

Both numbers are reported per era where the log shows a format or behaviour
cutover, because a single headline percentage averages over regimes that are
not comparable.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from mnemo.core import rule_activation
from mnemo.core.log_utils import iter_rotated_rows
from mnemo.core.mcp.popularity import load_recent_read_counts


def _query_of(entry: dict) -> str | None:
    """Return the call's query text, or None when it carried none."""
    value = (entry.get("args") or {}).get("query")
    return value if isinstance(value, str) and value.strip() else None


def _is_slug_shaped(value: str) -> bool:
    """True when *value* looks like a slug rather than a human rule name.

    The access log recorded ``hit_slugs`` as rule *names* until 2026-09-08 and
    as slugs after. Cases bootstrapped from the older rows can never match the
    activation index, so they are dropped as orphans for a reason that has
    nothing to do with recall quality.
    """
    return value == value.lower() and " " not in value


def measure_query_usage(log_path: Path) -> dict:
    """Count query-carrying vs query-less list_rules_by_topic calls."""
    calls = [
        e for e in iter_rotated_rows(log_path)
        if e.get("tool") == "list_rules_by_topic"
    ]
    calls.sort(key=lambda e: e.get("timestamp", ""))
    queried = [e for e in calls if _query_of(e)]
    unqueried = [e for e in calls if not _query_of(e)]

    last_unqueried = max((e["timestamp"] for e in unqueried), default=None)
    after_cutover = [e for e in calls if last_unqueried and e["timestamp"] > last_unqueried]

    by_day: collections.Counter[str] = collections.Counter()
    for entry in unqueried:
        by_day[entry.get("timestamp", "")[:10]] += 1

    return {
        "total": len(calls),
        "queried": len(queried),
        "unqueried": len(unqueried),
        "first_queried": min((e["timestamp"] for e in queried), default=None),
        "last_unqueried": last_unqueried,
        "calls_after_last_unqueried": len(after_cutover),
        "all_after_cutover_queried": all(_query_of(e) for e in after_cutover),
        "unqueried_by_day": dict(sorted(by_day.items())),
    }


def measure_hit_slug_format(log_path: Path) -> dict:
    """Track the name→slug format cutover in logged ``hit_slugs``."""
    per_month: dict[str, collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    first_slug: str | None = None
    last_name: str | None = None
    for entry in iter_rotated_rows(log_path):
        if entry.get("tool") != "list_rules_by_topic":
            continue
        hits = entry.get("hit_slugs") or []
        if not hits:
            continue
        timestamp = entry.get("timestamp", "")
        shape = "slug" if _is_slug_shaped(hits[0]) else "name"
        per_month[timestamp[:7]][shape] += 1
        if shape == "slug" and (first_slug is None or timestamp < first_slug):
            first_slug = timestamp
        if shape == "name" and (last_name is None or timestamp > last_name):
            last_name = timestamp
    return {
        "per_month": {k: dict(v) for k, v in sorted(per_month.items())},
        "first_slug_shaped": first_slug,
        "last_name_shaped": last_name,
    }


def _buckets(vault_root: Path) -> tuple[dict[tuple[str, str], list[str]], dict]:
    """Rebuild the (project, topic) candidate sets list_rules_by_topic serves."""
    index = rule_activation.load_index(vault_root)
    if index is None or "rules" not in index:
        raise SystemExit(f"error: no activation index under {vault_root}")
    rules = index["rules"]
    universal = set(index.get("universal", {}).get("slugs", []))

    buckets: dict[tuple[str, str], list[str]] = {}
    for project, entry in index.get("by_project", {}).items():
        candidates = set(entry.get("local_slugs", [])) | universal
        by_topic: dict[str, list[str]] = collections.defaultdict(list)
        for slug in candidates:
            for topic in rules.get(slug, {}).get("topic_tags", []):
                by_topic[topic].append(slug)
        for topic, slugs in by_topic.items():
            buckets[(project, topic)] = slugs
    return buckets, rules


def _tied_pair_share(buckets: dict, key_fn) -> tuple[int, int]:
    """Return (tied_pairs, total_pairs) under the given sort-key function.

    Two rules tie when *key_fn* gives them the same value: the sort cannot
    order them, so the next key decides. Counting pairs rather than rules makes
    buckets of different sizes commensurable.
    """
    total = tied = 0
    for slugs in buckets.values():
        n = len(slugs)
        if n < 2:
            continue
        groups = collections.Counter(key_fn(s) for s in slugs)
        total += n * (n - 1) // 2
        tied += sum(c * (c - 1) // 2 for c in groups.values())
    return tied, total


def measure_sort_key_discrimination(vault_root: Path) -> dict:
    """Quantify how often source_count (then popularity) fails to order a bucket."""
    buckets, rules = _buckets(vault_root)
    popularity = load_recent_read_counts(vault_root)

    def source_count(slug: str) -> tuple:
        return (rules[slug].get("source_count", 0),)

    def with_popularity(slug: str) -> tuple:
        return (rules[slug].get("source_count", 0), popularity.get(slug, 0))

    tied_sc, total = _tied_pair_share(buckets, source_count)
    tied_pop, _ = _tied_pair_share(buckets, with_popularity)

    distribution = collections.Counter(
        r.get("source_count", 0) for r in rules.values()
    )

    big = []
    for (project, topic), slugs in buckets.items():
        n = len(slugs)
        if n < 20:
            continue
        groups = collections.Counter(rules[s].get("source_count", 0) for s in slugs)
        pairs = n * (n - 1) // 2
        tied = sum(c * (c - 1) // 2 for c in groups.values())
        largest = max(groups.values())
        big.append({
            "bucket": f"{project}:{topic}",
            "rules": n,
            "largest_tie_group": largest,
            "tied_pair_pct": round(100 * tied / pairs, 1),
        })
    big.sort(key=lambda r: -r["rules"])

    return {
        "rules_indexed": len(rules),
        "source_count_distribution": dict(sorted(distribution.items())),
        "buckets": len(buckets),
        "total_pairs": total,
        "tied_on_source_count": tied_sc,
        "tied_pct_source_count": round(100 * tied_sc / total, 1) if total else 0.0,
        "tied_after_popularity": tied_pop,
        "tied_pct_after_popularity": round(100 * tied_pop / total, 1) if total else 0.0,
        "popularity_slugs_30d": len(popularity),
        "popularity_slugs_matching_index": sum(1 for s in popularity if s in rules),
        "big_buckets": big[:15],
    }


def _print_report(queries: dict, fmt: dict, sort: dict) -> None:
    print("=" * 62)
    print("(1) does the logged caller pass a query?")
    print("=" * 62)
    total = queries["total"] or 1
    print(f"  list_rules_by_topic calls : {queries['total']}")
    print(f"    with query              : {queries['queried']} "
          f"({100 * queries['queried'] / total:.1f}%)")
    print(f"    without query           : {queries['unqueried']} "
          f"({100 * queries['unqueried'] / total:.1f}%)")
    print(f"  first queried call        : {queries['first_queried']}")
    print(f"  last query-less call      : {queries['last_unqueried']}")
    print(f"  calls after that          : {queries['calls_after_last_unqueried']} "
          f"(all queried: {queries['all_after_cutover_queried']})")

    print()
    print("=" * 62)
    print("(1b) hit_slugs format cutover (name -> slug)")
    print("=" * 62)
    for month, shapes in fmt["per_month"].items():
        print(f"  {month}  name={shapes.get('name', 0):3d}  slug={shapes.get('slug', 0):3d}")
    print(f"  last name-shaped : {fmt['last_name_shaped']}")
    print(f"  first slug-shaped: {fmt['first_slug_shaped']}")

    print()
    print("=" * 62)
    print("(2) can source_count order a bucket?")
    print("=" * 62)
    print(f"  rules indexed: {sort['rules_indexed']}   buckets: {sort['buckets']}")
    print("  source_count distribution:")
    for value, count in sort["source_count_distribution"].items():
        share = 100 * count / sort["rules_indexed"]
        print(f"    source_count={value:<3d} rules={count:<5d} ({share:.1f}%)")
    print(f"  within-bucket pairs tied on source_count      : "
          f"{sort['tied_on_source_count']}/{sort['total_pairs']} "
          f"= {sort['tied_pct_source_count']}%")
    print(f"  still tied after the popularity tiebreak      : "
          f"{sort['tied_after_popularity']}/{sort['total_pairs']} "
          f"= {sort['tied_pct_after_popularity']}%")
    print(f"  popularity slugs (30d): {sort['popularity_slugs_30d']}, "
          f"matching index keys: {sort['popularity_slugs_matching_index']}")
    print()
    print("  biggest buckets:")
    print(f"    {'rules':>5} {'tiegrp':>7} {'tied%':>7}  bucket")
    for row in sort["big_buckets"]:
        print(f"    {row['rules']:5d} {row['largest_tie_group']:7d} "
              f"{row['tied_pair_pct']:6.1f}%  {row['bucket']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vault", type=Path, default=Path.home() / "mnemo",
        help="vault root (default ~/mnemo)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    vault = args.vault.expanduser()
    log_path = vault / ".mnemo" / "mcp-access-log.jsonl"
    if not log_path.is_file():
        print(f"error: access log missing: {log_path}", file=sys.stderr)
        return 1

    queries = measure_query_usage(log_path)
    fmt = measure_hit_slug_format(log_path)
    sort = measure_sort_key_discrimination(vault)

    if args.json:
        print(json.dumps(
            {"query_usage": queries, "hit_slug_format": fmt, "sort_keys": sort},
            indent=2,
        ))
    else:
        _print_report(queries, fmt, sort)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
