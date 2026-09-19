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

    def test_buried_is_split_from_absent(self) -> None:
        """#158: every historical "miss" was a rule *returned* at rank 6–65,
        never one missing from the result. The report must say which."""
        results = [
            self._result("top", 1),
            self._result("six", 6),
            self._result("deep", 11),
            self._result("gone", None),
        ]
        r = aggregate(results)
        assert r["buried"] == ["six", "deep"]   # returned, outside top-5
        assert r["absent"] == ["gone"]          # not returned at all
        assert r["buried_rank_max"] == 11
        # ``misses`` keeps its rank>10-or-absent meaning for the autopilot
        # digest and miss collector, which read it by name.
        assert r["misses"] == ["deep", "gone"]

    def test_buried_rank_max_is_none_when_nothing_is_buried(self) -> None:
        r = aggregate([self._result("a", 1), self._result("b", None)])
        assert r["buried"] == []
        assert r["absent"] == ["b"]
        assert r["buried_rank_max"] is None

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
        assert "misses (1, rank > 10 or absent)" in out
        assert "- gone" in out

    def test_outside_top5_line_separates_buried_from_absent(self) -> None:
        """#158: the headline must not let a buried rule read as a missing one."""
        def _r(id_, rank, n):
            return {
                "id": id_, "project": "p", "topic": "t", "expect_slug": "s",
                "hit": rank is not None and rank <= 10, "rank": rank,
                "result_count": n, "elapsed_ms": 1.0,
            }
        results = [_r("top", 1, 20), _r("six", 6, 20), _r("deep", 34, 41), _r("gone", None, 0)]
        out = format_report(aggregate(results), results)
        assert "outside top-5      : 3 = buried 2 (rank 6–34) + absent 1" in out
        # each listed miss says where the rule actually sat, and in how many
        assert "- deep  rank 34/41" in out
        assert "absent (1):" in out
        assert "- gone" in out

    def test_outside_top5_line_omits_range_when_nothing_buried(self) -> None:
        results = [{
            "id": "ok", "project": "p", "topic": "t", "expect_slug": "s",
            "hit": True, "rank": 1, "result_count": 1, "elapsed_ms": 1.0,
        }]
        out = format_report(aggregate(results), results)
        assert "outside top-5      : 0 = buried 0 + absent 0" in out
        assert "rank 1–" not in out
        assert "absent (" not in out

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

    def test_count_includes_the_rotated_file(self, tmp_path: Path) -> None:
        """#140: the phase-3 threshold counts every entry the bootstrap sees."""
        p = tmp_path / "log.jsonl"
        p.write_text('{"a":1}\n\n{"b":2}\n', encoding="utf-8")
        p.with_suffix(".jsonl.1").write_text('{"c":3}\n{"d":4}\n', encoding="utf-8")
        assert count_log_entries(p) == 4

    def test_count_only_rotated_file_present(self, tmp_path: Path) -> None:
        p = tmp_path / "log.jsonl"
        p.with_suffix(".jsonl.1").write_text('{"c":3}\n', encoding="utf-8")
        assert count_log_entries(p) == 1


class TestRotatedLog:
    """#140: the access log rotates to ``.1`` at 1 MiB; a list→read pair
    that straddles that boundary must still be found."""

    def test_pair_straddling_rotation_is_found(self, tmp_path: Path) -> None:
        log = tmp_path / "log.jsonl"
        _write_log(log.with_suffix(".jsonl.1"), [
            {
                "timestamp": "2026-04-17T10:00:00Z",
                "tool": "list_rules_by_topic",
                "args": {"topic": "workflow", "scope": "project"},
                "project": "mnemo",
                "hit_slugs": ["slug-a", "slug-b"],
            },
        ])
        _write_log(log, [
            {
                "timestamp": "2026-04-17T10:00:10Z",
                "tool": "read_mnemo_rule",
                "args": {"slug": "slug-b"},
                "project": "mnemo",
            },
        ])
        cases = bootstrap_cases(log)
        assert [c["expect_slug"] for c in cases] == ["slug-b"]

    def test_pair_survives_an_undecodable_byte(self, tmp_path: Path) -> None:
        log = tmp_path / "log.jsonl"
        _write_log(log, [
            {
                "timestamp": "2026-04-17T10:00:00Z",
                "tool": "list_rules_by_topic",
                "args": {"topic": "workflow", "scope": "project"},
                "project": "mnemo",
                "hit_slugs": ["slug-a"],
            },
            {
                "timestamp": "2026-04-17T10:00:05Z",
                "tool": "read_mnemo_rule",
                "args": {"slug": "slug-a"},
                "project": "mnemo",
            },
        ])
        with log.open("ab") as fh:
            fh.write(b'{"tool": "read_mnemo_rule", "args": {"slug": "\xff"}}\n')
        assert [c["expect_slug"] for c in bootstrap_cases(log)] == ["slug-a"]


class TestQueryCarriedThrough:
    """#158: real callers pass ``query`` since #105; the harness must too."""

    def _log_with_query(self, tmp_path: Path, query: str | None) -> Path:
        log = tmp_path / "log.jsonl"
        args = {"topic": "workflow", "scope": "project"}
        if query is not None:
            args["query"] = query
        _write_log(log, [
            {
                "timestamp": "2026-08-31T10:00:00Z",
                "tool": "list_rules_by_topic",
                "args": args,
                "project": "mnemo",
                "hit_slugs": ["slug-a", "slug-b"],
            },
            {
                "timestamp": "2026-08-31T10:00:10Z",
                "tool": "read_mnemo_rule",
                "args": {"slug": "slug-b"},
                "project": "mnemo",
            },
        ])
        return log

    def test_bootstrap_records_query(self, tmp_path: Path) -> None:
        cases = bootstrap_cases(self._log_with_query(tmp_path, "rotate log files"))
        assert len(cases) == 1
        assert cases[0]["query"] == "rotate log files"
        assert cases[0]["id"] == "mnemo:workflow:slug-b?q"

    def test_bootstrap_omits_query_key_when_absent(self, tmp_path: Path) -> None:
        cases = bootstrap_cases(self._log_with_query(tmp_path, None))
        assert len(cases) == 1
        assert "query" not in cases[0]
        assert cases[0]["id"] == "mnemo:workflow:slug-b"

    def test_bootstrap_keeps_queried_and_unqueried_as_separate_cases(self, tmp_path: Path) -> None:
        log = tmp_path / "log.jsonl"
        _write_log(log, [
            {
                "timestamp": "2026-08-01T10:00:00Z",
                "tool": "list_rules_by_topic",
                "args": {"topic": "workflow"},
                "project": "mnemo",
                "hit_slugs": ["slug-b"],
            },
            {
                "timestamp": "2026-08-01T10:00:05Z",
                "tool": "read_mnemo_rule",
                "args": {"slug": "slug-b"},
                "project": "mnemo",
            },
            {
                "timestamp": "2026-08-31T10:00:00Z",
                "tool": "list_rules_by_topic",
                "args": {"topic": "workflow", "query": "log rotation"},
                "project": "mnemo",
                "hit_slugs": ["slug-b"],
            },
            {
                "timestamp": "2026-08-31T10:00:05Z",
                "tool": "read_mnemo_rule",
                "args": {"slug": "slug-b"},
                "project": "mnemo",
            },
        ])
        cases = bootstrap_cases(log)
        assert [c["id"] for c in cases] == [
            "mnemo:workflow:slug-b",
            "mnemo:workflow:slug-b?q",
        ]

    def test_run_case_passes_query_to_retrieval(self, tmp_vault: Path, monkeypatch) -> None:
        seen: dict = {}

        def fake_list(vault_root, topic, *, scope, project, query=None):
            seen["query"] = query
            return [{"slug": "rule-one"}]

        monkeypatch.setattr("mnemo.core.mcp.recall.list_rules_by_topic", fake_list)
        case = {
            "id": "proj-x:workflow:rule-one?q",
            "project": "proj-x",
            "topic": "workflow",
            "expect_slug": "rule-one",
            "rank_at_bootstrap": 1,
            "query": "rotate the log",
        }
        r = run_case(tmp_vault, case)
        assert seen["query"] == "rotate the log"
        assert r["query"] == "rotate the log"

    def test_run_case_without_query_sends_none(self, tmp_vault: Path, monkeypatch) -> None:
        seen: dict = {}

        def fake_list(vault_root, topic, *, scope, project, query=None):
            seen["query"] = query
            return []

        monkeypatch.setattr("mnemo.core.mcp.recall.list_rules_by_topic", fake_list)
        case = {
            "id": "proj-x:workflow:rule-one",
            "project": "proj-x",
            "topic": "workflow",
            "expect_slug": "rule-one",
            "rank_at_bootstrap": 1,
        }
        r = run_case(tmp_vault, case)
        assert seen["query"] is None
        assert r["query"] is None


class TestAggregateQuerySplit:
    def _result(self, id_: str, rank: int | None, query: str | None):
        return {
            "id": id_,
            "project": "p",
            "topic": "t",
            "expect_slug": "s",
            "hit": rank is not None and rank <= 10,
            "rank": rank,
            "result_count": 0,
            "elapsed_ms": 1.0,
            "query": query,
        }

    def test_split_counts_and_rates(self) -> None:
        r = aggregate([
            self._result("a", 1, "q1"),
            self._result("b", 8, "q2"),
            self._result("c", 3, None),
            self._result("d", None, None),
        ])
        assert r["queried"] == {"cases": 2, "primacy_at_5": 1, "primacy_rate_at_5": 0.5, "mrr": 0.5625}
        assert r["unqueried"] == {"cases": 2, "primacy_at_5": 1, "primacy_rate_at_5": 0.5, "mrr": 0.1667}

    def test_split_tolerates_results_without_query_key(self) -> None:
        legacy = {
            "id": "a", "project": "p", "topic": "t", "expect_slug": "s",
            "hit": True, "rank": 1, "result_count": 1, "elapsed_ms": 1.0,
        }
        r = aggregate([legacy])
        assert r["queried"]["cases"] == 0
        assert r["unqueried"]["cases"] == 1

    def test_format_report_shows_split_when_queried_present(self) -> None:
        r = aggregate([self._result("a", 1, "q1"), self._result("c", 3, None)])
        text = format_report(r)
        assert "with query" in text
        assert "without query" in text

    def test_format_report_hides_split_when_no_queried_cases(self) -> None:
        r = aggregate([self._result("c", 3, None)])
        assert "with query" not in format_report(r)


class TestVocabularyDiagnostic:
    """#381: a rank cannot say whether re-ranking could have reached the rule.

    The diagnostic separates the two: a rule that shares no indexed token with
    the query scores zero and is unreachable by any weighting, while one that
    scores and still loses is a genuine ranking failure.
    """

    def _case(self, slug: str = "rule-one", query: str | None = None) -> dict:
        case = {
            "id": f"proj-x:workflow:{slug}",
            "project": "proj-x",
            "topic": "workflow",
            "expect_slug": slug,
            "rank_at_bootstrap": 1,
        }
        if query is not None:
            case["query"] = query
        return case

    def _index(self, tf_by_slug: dict[str, dict[str, int]]) -> dict:
        """A hand-built reflex index: {slug: {term: tf_in_body}}."""
        postings: dict[str, list[dict]] = {}
        docs: dict[str, dict] = {}
        for slug, terms in tf_by_slug.items():
            docs[slug] = {"field_length": {"body": sum(terms.values()) or 1}}
            for term, tf in terms.items():
                postings.setdefault(term, []).append(
                    {"slug": slug, "tf": {"body": tf}}
                )
        return {
            "schema_version": 1,
            "doc_count": len(docs),
            "avg_field_length": {"body": 1.0},
            "postings": postings,
            "docs": docs,
        }

    def test_unqueried_case_is_unmeasured_not_zero(self, tmp_vault: Path) -> None:
        _seed_rule(tmp_vault, "rule-one", "feedback", ["workflow"], "proj-x")
        r = run_case(tmp_vault, self._case())
        assert r["query_tokens"] is None
        assert r["query_tokens_matched"] is None
        assert r["bm25_score"] is None

    def test_missing_reflex_index_is_unmeasured_not_zero(self, tmp_vault: Path) -> None:
        """No index is the corpus retrieval itself falls back from.

        Reporting coverage 0 here would invent a vocabulary gap out of an
        absent file and put the case in ``vocabulary_gap``, which reads as
        "no fix can reach this rule".
        """
        _seed_rule(tmp_vault, "rule-one", "feedback", ["workflow"], "proj-x")
        r = run_case(tmp_vault, self._case(query="deploy the release"))
        assert r["query_tokens"] is None
        assert r["query_tokens_matched"] is None
        assert r["bm25_score"] is None

    def test_shared_vocabulary_is_counted_and_scored(self, tmp_vault: Path) -> None:
        _seed_rule(tmp_vault, "rule-one", "feedback", ["workflow"], "proj-x")
        idx = self._index({
            "rule-one": {"deploy": 3, "release": 1},
            "other": {"unrelated": 1},
        })
        r = run_case(
            tmp_vault,
            self._case(query="deploy the release pipeline"),
            reflex_index=idx,
        )
        assert r["query_tokens"] == 3  # "the" is a stopword
        assert r["query_tokens_matched"] == 2
        assert r["bm25_score"] > 0

    def test_no_shared_vocabulary_scores_zero(self, tmp_vault: Path) -> None:
        _seed_rule(tmp_vault, "rule-one", "feedback", ["workflow"], "proj-x")
        idx = self._index({"rule-one": {"kubernetes": 4}})
        r = run_case(
            tmp_vault,
            self._case(query="deploy the release pipeline"),
            reflex_index=idx,
        )
        assert r["query_tokens_matched"] == 0
        assert r["bm25_score"] == 0.0

    def test_passed_index_is_used_without_touching_disk(self, tmp_vault: Path) -> None:
        """The sweep loads the vault's largest file once, not once per case."""
        _seed_rule(tmp_vault, "rule-one", "feedback", ["workflow"], "proj-x")
        assert not (tmp_vault / ".mnemo" / "reflex-index.json").exists()
        idx = self._index({"rule-one": {"deploy": 1}})
        r = run_case(tmp_vault, self._case(query="deploy"), reflex_index=idx)
        assert r["query_tokens_matched"] == 1


class TestOutsideTop5Split:
    def _result(
        self,
        id_: str,
        rank: int | None,
        *,
        query: str | None = "q",
        matched: int | None = 1,
    ) -> dict:
        return {
            "id": id_,
            "project": "p",
            "topic": "t",
            "expect_slug": "s",
            "hit": rank is not None and rank <= 10,
            "rank": rank,
            "result_count": 0,
            "elapsed_ms": 1.0,
            "query": query,
            "query_tokens": None if matched is None else 5,
            "query_tokens_matched": matched,
            "bm25_score": None if matched is None else float(matched),
        }

    def test_splits_unreachable_from_outranked(self) -> None:
        r = aggregate([
            self._result("top", 2),
            self._result("gap", 20, matched=0),
            self._result("lost", 9, matched=3),
            self._result("gone", None, matched=0),
        ])
        assert r["vocabulary_gap"] == ["gap", "gone"]
        assert r["outranked"] == ["lost"]

    def test_unqueried_cases_are_in_neither(self) -> None:
        """An unqueried case never reaches BM25F, so it has no vocabulary gap."""
        r = aggregate([self._result("legacy", 30, query=None, matched=None)])
        assert r["vocabulary_gap"] == []
        assert r["outranked"] == []

    def test_unmeasured_queried_case_is_in_neither(self) -> None:
        r = aggregate([self._result("noindex", 30, matched=None)])
        assert r["vocabulary_gap"] == []
        assert r["outranked"] == []

    def test_top5_case_is_never_counted(self) -> None:
        r = aggregate([self._result("fine", 5, matched=0)])
        assert r["vocabulary_gap"] == []
        assert r["outranked"] == []

    def test_format_report_names_both_populations(self) -> None:
        out = format_report(aggregate([
            self._result("gap", 20, matched=0),
            self._result("lost", 9, matched=3),
        ]))
        assert "1 share no token with their query" in out
        assert "1 scored but outranked" in out

    def test_format_report_omits_line_when_nothing_outside_top5(self) -> None:
        out = format_report(aggregate([self._result("fine", 1)]))
        assert "share no token" not in out
