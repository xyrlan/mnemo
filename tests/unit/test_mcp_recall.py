"""Unit tests for the recall harness — pure bootstrap/score/aggregate logic."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core.mcp.recall import (
    PHASE3_THRESHOLD,
    aggregate,
    bootstrap_cases,
    count_log_entries,
    format_report,
    run_case,
)


def _write_log(path: Path, entries: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n",
        encoding="utf-8",
    )


def _seed_rule(vault: Path, slug: str, page_type: str, tags: list[str], project: str) -> None:
    d = vault / "shared" / page_type
    d.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"type: {page_type}",
        "tags:",
    ]
    lines.extend(f"  - {t}" for t in tags)
    lines.append("sources:")
    lines.append(f"  - bots/{project}/memory/{slug}.md")
    lines.append("---")
    lines.append("")
    lines.append("body")
    (d / f"{slug}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestBootstrapCases:
    def test_missing_log_returns_empty(self, tmp_path: Path) -> None:
        assert bootstrap_cases(tmp_path / "no.jsonl") == []

    def test_pairs_list_then_read_within_window(self, tmp_path: Path) -> None:
        log = tmp_path / "log.jsonl"
        _write_log(log, [
            {
                "timestamp": "2026-04-17T10:00:00Z",
                "tool": "list_rules_by_topic",
                "args": {"topic": "workflow", "scope": "project"},
                "project": "mnemo",
                "hit_slugs": ["slug-a", "slug-b"],
            },
            {
                "timestamp": "2026-04-17T10:00:10Z",
                "tool": "read_mnemo_rule",
                "args": {"slug": "slug-b"},
                "project": "mnemo",
            },
        ])
        cases = bootstrap_cases(log)
        assert len(cases) == 1
        c = cases[0]
        assert c["project"] == "mnemo"
        assert c["topic"] == "workflow"
        assert c["expect_slug"] == "slug-b"
        assert c["rank_at_bootstrap"] == 2

    def test_pair_beyond_window_is_skipped(self, tmp_path: Path) -> None:
        log = tmp_path / "log.jsonl"
        _write_log(log, [
            {
                "timestamp": "2026-04-17T10:00:00Z",
                "tool": "list_rules_by_topic",
                "args": {"topic": "workflow"},
                "project": "mnemo",
                "hit_slugs": ["slug-a"],
            },
            {
                "timestamp": "2026-04-17T10:05:00Z",  # 5 min later
                "tool": "read_mnemo_rule",
                "args": {"slug": "slug-a"},
                "project": "mnemo",
            },
        ])
        assert bootstrap_cases(log, pair_window_s=120) == []

    def test_read_slug_not_in_returned_list_is_skipped(self, tmp_path: Path) -> None:
        log = tmp_path / "log.jsonl"
        _write_log(log, [
            {
                "timestamp": "2026-04-17T10:00:00Z",
                "tool": "list_rules_by_topic",
                "args": {"topic": "workflow"},
                "project": "mnemo",
                "hit_slugs": ["slug-a"],
            },
            {
                "timestamp": "2026-04-17T10:00:30Z",
                "tool": "read_mnemo_rule",
                "args": {"slug": "slug-z"},  # not in returned list
                "project": "mnemo",
            },
        ])
        assert bootstrap_cases(log) == []

    def test_dedup_same_triple(self, tmp_path: Path) -> None:
        log = tmp_path / "log.jsonl"
        entries = []
        for i in range(3):
            entries.append({
                "timestamp": f"2026-04-17T10:0{i}:00Z",
                "tool": "list_rules_by_topic",
                "args": {"topic": "workflow"},
                "project": "mnemo",
                "hit_slugs": ["slug-a"],
            })
            entries.append({
                "timestamp": f"2026-04-17T10:0{i}:05Z",
                "tool": "read_mnemo_rule",
                "args": {"slug": "slug-a"},
                "project": "mnemo",
            })
        _write_log(log, entries)
        assert len(bootstrap_cases(log)) == 1

    def test_project_isolation(self, tmp_path: Path) -> None:
        log = tmp_path / "log.jsonl"
        _write_log(log, [
            {
                "timestamp": "2026-04-17T10:00:00Z",
                "tool": "list_rules_by_topic",
                "args": {"topic": "workflow"},
                "project": "project-a",
                "hit_slugs": ["slug-a"],
            },
            {
                "timestamp": "2026-04-17T10:00:10Z",
                "tool": "read_mnemo_rule",
                "args": {"slug": "slug-a"},
                "project": "project-b",  # different project
            },
        ])
        assert bootstrap_cases(log) == []

    def test_malformed_lines_are_skipped(self, tmp_path: Path) -> None:
        log = tmp_path / "log.jsonl"
        log.write_text(
            'not json\n'
            '{"timestamp": "2026-04-17T10:00:00Z", "tool": "list_rules_by_topic", '
            '"args": {"topic": "workflow"}, "project": "mnemo", "hit_slugs": ["slug-a"]}\n'
            '\n'
            '{"timestamp": "2026-04-17T10:00:10Z", "tool": "read_mnemo_rule", '
            '"args": {"slug": "slug-a"}, "project": "mnemo"}\n',
            encoding="utf-8",
        )
        assert len(bootstrap_cases(log)) == 1


class TestRunCase:
    def test_hit_returns_rank(self, tmp_vault: Path) -> None:
        _seed_rule(tmp_vault, "rule-one", "feedback", ["workflow"], "proj-x")
        case = {
            "id": "proj-x:workflow:rule-one",
            "project": "proj-x",
            "topic": "workflow",
            "expect_slug": "rule-one",
            "rank_at_bootstrap": 1,
        }
        r = run_case(tmp_vault, case)
        assert r["hit"] is True
        assert r["rank"] == 1
        assert r["result_count"] == 1
        assert r["elapsed_ms"] >= 0

    def test_miss_when_slug_absent(self, tmp_vault: Path) -> None:
        _seed_rule(tmp_vault, "other-rule", "feedback", ["workflow"], "proj-x")
        case = {
            "id": "proj-x:workflow:ghost",
            "project": "proj-x",
            "topic": "workflow",
            "expect_slug": "ghost",
            "rank_at_bootstrap": 1,
        }
        r = run_case(tmp_vault, case)
        assert r["hit"] is False
        assert r["rank"] is None
        assert r["result_count"] == 1


class TestAggregate:
    def _result(self, id_: str, rank: int | None, elapsed_ms: float = 1.0):
        return {
            "id": id_,
            "project": "p",
            "topic": "t",
            "expect_slug": "s",
            "hit": rank is not None and rank <= 10,
            "rank": rank,
            "result_count": 0,
            "elapsed_ms": elapsed_ms,
        }

    def test_empty_report(self) -> None:
        r = aggregate([])
        assert r["cases"] == 0
        assert r["mrr"] == 0.0
        assert r["p95_latency_ms"] == 0.0
        assert r["log_entries"] is None
        assert r["phase3_threshold"] == PHASE3_THRESHOLD

    def test_all_hits_top_one(self) -> None:
        results = [self._result(f"c{i}", 1) for i in range(5)]
        r = aggregate(results)
        assert r["cases"] == 5
        assert r["primacy_at_3"] == 5
        assert r["primacy_rate_at_3"] == 1.0
        assert r["mrr"] == 1.0
        assert r["misses"] == []

    def test_mixed_ranks(self) -> None:
        results = [
            self._result("a", 1),
            self._result("b", 2),
            self._result("c", 4),
            self._result("d", None),
        ]
        r = aggregate(results)
        assert r["primacy_at_3"] == 2
        assert r["primacy_at_5"] == 3
        assert r["primacy_at_10"] == 3
        assert r["misses"] == ["d"]
        # MRR = (1/1 + 1/2 + 1/4 + 0) / 4 = 1.75 / 4 = 0.4375
        assert r["mrr"] == pytest.approx(0.4375)

    def test_p95_reflects_slowest(self) -> None:
        latencies = [1.0, 2.0, 3.0, 4.0, 100.0]
        results = [self._result(f"c{i}", 1, l) for i, l in enumerate(latencies)]
        r = aggregate(results)
        assert r["p95_latency_ms"] == 100.0

    def test_log_entries_stored_when_provided(self) -> None:
        r = aggregate([self._result("a", 1)], log_entries=42)
        assert r["log_entries"] == 42
        assert r["phase3_threshold"] == PHASE3_THRESHOLD


class TestFormatReport:
    def test_renders_without_misses(self) -> None:
        report = aggregate([{
            "id": "ok",
            "project": "p",
            "topic": "t",
            "expect_slug": "s",
            "hit": True,
            "rank": 1,
            "result_count": 1,
            "elapsed_ms": 1.0,
        }])
        out = format_report(report)
        assert "cases              : 1" in out
        assert "primacy@3 / @5 /@10: 1 / 1 / 1" in out
        assert "MRR                : 1.0000" in out
        assert "misses" not in out

    def test_renders_with_misses(self) -> None:
        report = aggregate([{
            "id": "gone",
            "project": "p",
            "topic": "t",
            "expect_slug": "s",
            "hit": False,
            "rank": None,
            "result_count": 0,
            "elapsed_ms": 1.0,
        }])
        out = format_report(report)
        assert "misses (1)" in out
        assert "- gone" in out

    def test_footer_when_below_threshold(self) -> None:
        report = aggregate(
            [{
                "id": "ok", "project": "p", "topic": "t", "expect_slug": "s",
                "hit": True, "rank": 1, "result_count": 1, "elapsed_ms": 1.0,
            }],
            log_entries=14,
        )
        out = format_report(report)
        assert f"next ranking change unlocks at ≥{PHASE3_THRESHOLD}" in out
        assert "currently 14" in out

    def test_footer_when_threshold_met(self) -> None:
        report = aggregate(
            [{
                "id": "ok", "project": "p", "topic": "t", "expect_slug": "s",
                "hit": True, "rank": 1, "result_count": 1, "elapsed_ms": 1.0,
            }],
            log_entries=PHASE3_THRESHOLD,
        )
        out = format_report(report)
        assert "phase-3 ranking-change threshold met" in out

    def test_footer_absent_when_log_entries_none(self) -> None:
        report = aggregate([{
            "id": "ok", "project": "p", "topic": "t", "expect_slug": "s",
            "hit": True, "rank": 1, "result_count": 1, "elapsed_ms": 1.0,
        }])
        out = format_report(report)
        assert "unlocks at" not in out
        assert "threshold met" not in out


class TestCountLogEntries:
    def test_missing_returns_zero(self, tmp_path: Path) -> None:
        assert count_log_entries(tmp_path / "no.jsonl") == 0

    def test_counts_non_blank_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "log.jsonl"
        p.write_text('{"a":1}\n\n{"b":2}\n   \n{"c":3}\n', encoding="utf-8")
        assert count_log_entries(p) == 3
