"""Tests for mnemo.core.mcp.access_log_summary — log aggregator."""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core.mcp.access_log_summary import (
    format_human,
    read_log,
    summarize,
)


def _entry(**overrides) -> dict:
    base = {
        "timestamp": "2026-04-16T12:00:00Z",
        "tool": "list_rules_by_topic",
        "args": {"topic": "git", "scope": "project"},
        "scope_requested": "project",
        "scope_effective": "project",
        "project": "mnemo",
        "result_count": 2,
        "hit_slugs": ["rule-a", "rule-b"],
        "elapsed_ms": 1.5,
    }
    base.update(overrides)
    return base


# ---------- summarize() ----------

def test_summarize_empty_entries_returns_zero_shape():
    result = summarize([])
    assert result["total_calls"] == 0
    assert result["zero_hit_calls"] == 0
    assert result["zero_hit_rate"] == 0.0
    assert result["by_tool"] == {}
    assert result["by_project"] == {}


def test_summarize_counts_total_calls():
    entries = [_entry(), _entry(), _entry()]
    result = summarize(entries)
    assert result["total_calls"] == 3


def test_summarize_groups_by_tool():
    entries = [
        _entry(tool="list_rules_by_topic"),
        _entry(tool="list_rules_by_topic"),
        _entry(tool="read_mnemo_rule"),
        _entry(tool="get_mnemo_topics"),
    ]
    result = summarize(entries)
    assert result["by_tool"] == {
        "list_rules_by_topic": 2,
        "read_mnemo_rule": 1,
        "get_mnemo_topics": 1,
    }


def test_summarize_counts_zero_hits():
    entries = [
        _entry(result_count=2),
        _entry(result_count=0),
        _entry(result_count=5),
        _entry(result_count=0),
    ]
    result = summarize(entries)
    assert result["zero_hit_calls"] == 2
    assert result["zero_hit_rate"] == 0.5


def test_summarize_zero_hit_rate_rounded():
    entries = [_entry(result_count=0), _entry(result_count=1), _entry(result_count=1)]
    result = summarize(entries)
    assert result["zero_hit_rate"] == round(1 / 3, 4)


def test_summarize_groups_by_project():
    entries = [
        _entry(project="mnemo", result_count=2),
        _entry(project="mnemo", result_count=0),
        _entry(project="sg-imports", result_count=5),
    ]
    result = summarize(entries)
    assert result["by_project"]["mnemo"]["calls"] == 2
    assert result["by_project"]["mnemo"]["zero_hit"] == 1
    assert result["by_project"]["mnemo"]["zero_hit_rate"] == 0.5
    assert result["by_project"]["sg-imports"]["calls"] == 1
    assert result["by_project"]["sg-imports"]["zero_hit"] == 0
    assert result["by_project"]["sg-imports"]["zero_hit_rate"] == 0.0


def test_summarize_bucketizes_null_project():
    entries = [_entry(project=None, result_count=0)]
    result = summarize(entries)
    assert "(unresolved)" in result["by_project"]
    assert result["by_project"]["(unresolved)"]["calls"] == 1
    assert result["by_project"]["(unresolved)"]["zero_hit"] == 1


def test_summarize_skips_entries_missing_required_fields():
    entries = [_entry(), {"tool": "list_rules_by_topic"}]  # second lacks result_count/project
    result = summarize(entries)
    assert result["total_calls"] == 1


# ---------- read_log() ----------

def test_read_log_returns_empty_when_missing(tmp_path):
    assert read_log(tmp_path) == []


def test_read_log_parses_current_file(tmp_path):
    vault = tmp_path
    (vault / ".mnemo").mkdir()
    log = vault / ".mnemo" / "mcp-access-log.jsonl"
    lines = [json.dumps(_entry(tool="a")), json.dumps(_entry(tool="b"))]
    log.write_text("\n".join(lines) + "\n")
    entries = read_log(vault)
    assert [e["tool"] for e in entries] == ["a", "b"]


def test_read_log_skips_malformed_lines(tmp_path):
    vault = tmp_path
    (vault / ".mnemo").mkdir()
    log = vault / ".mnemo" / "mcp-access-log.jsonl"
    log.write_text(json.dumps(_entry(tool="a")) + "\nnot-json\n" + json.dumps(_entry(tool="b")) + "\n")
    entries = read_log(vault)
    assert [e["tool"] for e in entries] == ["a", "b"]


def test_read_log_includes_rotated(tmp_path):
    vault = tmp_path
    (vault / ".mnemo").mkdir()
    (vault / ".mnemo" / "mcp-access-log.jsonl").write_text(json.dumps(_entry(tool="current")) + "\n")
    (vault / ".mnemo" / "mcp-access-log.jsonl.1").write_text(json.dumps(_entry(tool="old")) + "\n")
    entries = read_log(vault)
    tools = [e["tool"] for e in entries]
    assert set(tools) == {"current", "old"}


# ---------- format_human() ----------

def test_format_human_renders_summary_sections():
    summary = summarize([
        _entry(project="mnemo", tool="list_rules_by_topic", result_count=5),
        _entry(project="mnemo", tool="read_mnemo_rule", result_count=1),
        _entry(project="bingx-robot", tool="list_rules_by_topic", result_count=0),
    ])
    out = format_human(summary)
    assert "Total calls: 3" in out
    assert "Zero-hit" in out
    assert "mnemo" in out
    assert "bingx-robot" in out
    assert "list_rules_by_topic" in out


def test_format_human_handles_empty_summary():
    out = format_human(summarize([]))
    assert "Total calls: 0" in out
    assert "no data" in out.lower() or "no entries" in out.lower()
