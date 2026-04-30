from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mnemo.autopilot.insights._log_readers import (
    read_mcp_access_log,
    read_reflex_log,
    read_denial_log,
    read_recall_report,
)

_MNemo = ".mnemo"


def _write_jsonl(path: Path, entries: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def _iso(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# ── mcp-access-log ──────────────────────────────────────────────────────────

def test_read_mcp_access_log_missing_file(tmp_path: Path):
    result = read_mcp_access_log(tmp_path, since_dt=_iso("2026-01-01T00:00:00Z"))
    assert result == []


def test_read_mcp_access_log_filters_by_since(tmp_path: Path):
    entries = [
        {"timestamp": "2026-04-23T08:00:00Z", "tool": "list_rules_by_topic"},
        {"timestamp": "2026-04-24T08:00:00Z", "tool": "read_mnemo_rule"},
        {"timestamp": "2026-04-20T08:00:00Z", "tool": "list_rules_by_topic"},
    ]
    _write_jsonl(tmp_path / _MNemo / "mcp-access-log.jsonl", entries)
    since = _iso("2026-04-22T00:00:00Z")
    result = read_mcp_access_log(tmp_path, since_dt=since)
    assert len(result) == 2
    assert all(e["timestamp"] >= "2026-04-22T00:00:00Z" for e in result)


def test_read_mcp_access_log_skips_malformed(tmp_path: Path):
    path = tmp_path / _MNemo / "mcp-access-log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"timestamp": "2026-04-25T00:00:00Z"}\n{invalid}\n')
    since = _iso("2026-01-01T00:00:00Z")
    result = read_mcp_access_log(tmp_path, since_dt=since)
    assert len(result) == 1


# ── reflex-log ──────────────────────────────────────────────────────────────

def test_read_reflex_log_missing(tmp_path: Path):
    result = read_reflex_log(tmp_path, since_dt=_iso("2026-01-01T00:00:00Z"))
    assert result == []


def test_read_reflex_log_filters_by_since(tmp_path: Path):
    entries = [
        {"ts": "2026-04-25T10:00:00Z", "emitted": True, "session_id": "abc"},
        {"ts": "2026-04-20T10:00:00Z", "emitted": False, "session_id": "def"},
    ]
    _write_jsonl(tmp_path / _MNemo / "reflex-log.jsonl", entries)
    since = _iso("2026-04-22T00:00:00Z")
    result = read_reflex_log(tmp_path, since_dt=since)
    assert len(result) == 1
    assert result[0]["ts"] == "2026-04-25T10:00:00Z"


def test_read_reflex_log_skips_malformed(tmp_path: Path):
    path = tmp_path / _MNemo / "reflex-log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"ts": "2026-04-25T00:00:00Z"}\nnot-json\n')
    since = _iso("2026-01-01T00:00:00Z")
    result = read_reflex_log(tmp_path, since_dt=since)
    assert len(result) == 1


# ── denial-log ──────────────────────────────────────────────────────────────

def test_read_denial_log_missing(tmp_path: Path):
    result = read_denial_log(tmp_path, since_dt=_iso("2026-01-01T00:00:00Z"))
    assert result == []


def test_read_denial_log_filters_by_since(tmp_path: Path):
    entries = [
        {"timestamp": "2026-04-25T10:00:00Z", "slug": "some-rule", "project": "p"},
        {"timestamp": "2026-04-20T10:00:00Z", "slug": "old-rule", "project": "p"},
    ]
    _write_jsonl(tmp_path / _MNemo / "denial-log.jsonl", entries)
    since = _iso("2026-04-22T00:00:00Z")
    result = read_denial_log(tmp_path, since_dt=since)
    assert len(result) == 1
    assert result[0]["slug"] == "some-rule"


# ── recall-report ────────────────────────────────────────────────────────────

def test_read_recall_report_missing(tmp_path: Path):
    result = read_recall_report(tmp_path)
    assert result is None


def test_read_recall_report_valid(tmp_path: Path):
    data = {
        "generated_at": "2026-04-30T10:00:00Z",
        "report": {"primacy_rate_at_5": 0.9, "mrr": 0.55, "p95_latency_ms": 3.0,
                   "cases": 10, "misses": ["m1"]},
        "results": [
            {"id": "c1", "hit": True, "rank": 1, "expect_slug": "slug-a",
             "topic": "foo", "project": "p", "result_count": 5, "elapsed_ms": 1.0},
            {"id": "c2", "hit": False, "rank": None, "expect_slug": "slug-b",
             "topic": "bar", "project": "p", "result_count": 5, "elapsed_ms": 2.0},
        ],
    }
    path = tmp_path / _MNemo / "recall-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    result = read_recall_report(tmp_path)
    assert result is not None
    assert result["report"]["primacy_rate_at_5"] == 0.9
    assert len(result["results"]) == 2


def test_read_recall_report_malformed(tmp_path: Path):
    path = tmp_path / _MNemo / "recall-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not-json")
    result = read_recall_report(tmp_path)
    assert result is None
