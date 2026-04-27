"""Tests for mnemo.core.mcp.popularity and the popularity tiebreak in
``list_rules_by_topic`` (recall regression — see briefing 2026-04-27)."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mnemo.core.mcp.popularity import load_recent_read_counts
from mnemo.core.mcp.tools import list_rules_by_topic


def _write_log(vault: Path, entries: list[dict]) -> Path:
    log_dir = vault / ".mnemo"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "mcp-access-log.jsonl"
    with log_path.open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    return log_path


def _ts(now: datetime, days_ago: float) -> str:
    return (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_load_recent_read_counts_missing_log_returns_empty(tmp_path):
    assert load_recent_read_counts(tmp_path) == Counter()


def test_load_recent_read_counts_counts_only_read_mnemo_rule(tmp_path):
    now = datetime.now(timezone.utc)
    _write_log(tmp_path, [
        {"tool": "read_mnemo_rule", "timestamp": _ts(now, 1), "args": {"slug": "alpha"}},
        {"tool": "read_mnemo_rule", "timestamp": _ts(now, 2), "args": {"slug": "alpha"}},
        {"tool": "list_rules_by_topic", "timestamp": _ts(now, 1), "args": {"topic": "git"}},
        {"tool": "read_mnemo_rule", "timestamp": _ts(now, 3), "args": {"slug": "beta"}},
    ])
    counts = load_recent_read_counts(tmp_path, now=now)
    assert counts == Counter({"alpha": 2, "beta": 1})


def test_load_recent_read_counts_window_excludes_old_entries(tmp_path):
    now = datetime.now(timezone.utc)
    _write_log(tmp_path, [
        {"tool": "read_mnemo_rule", "timestamp": _ts(now, 5), "args": {"slug": "fresh"}},
        {"tool": "read_mnemo_rule", "timestamp": _ts(now, 90), "args": {"slug": "stale"}},
    ])
    counts = load_recent_read_counts(tmp_path, window_days=30, now=now)
    assert counts == Counter({"fresh": 1})


def test_load_recent_read_counts_skips_malformed_lines(tmp_path):
    now = datetime.now(timezone.utc)
    log_dir = tmp_path / ".mnemo"
    log_dir.mkdir()
    log_path = log_dir / "mcp-access-log.jsonl"
    log_path.write_text(
        "not json\n"
        + json.dumps({"tool": "read_mnemo_rule", "timestamp": _ts(now, 1),
                      "args": {"slug": "ok"}}) + "\n"
        + json.dumps({"tool": "read_mnemo_rule", "timestamp": "garbage",
                      "args": {"slug": "skipme"}}) + "\n"
    )
    assert load_recent_read_counts(tmp_path, now=now) == Counter({"ok": 1})


def test_list_rules_by_topic_popularity_breaks_source_count_tie(tmp_vault):
    """Two rules tie at source_count=1; the one read more recently sorts first."""
    from tests.unit.test_mcp_tools import _write_page

    _write_page(
        tmp_vault, "feedback", "rule-alpha",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],
    )
    _write_page(
        tmp_vault, "feedback", "rule-beta",
        tags=["auto-promoted", "git"],
        sources=["bots/a/n.md"],
    )
    # Without popularity, the alphabetical fallback puts rule-alpha first.
    baseline = list_rules_by_topic(tmp_vault, "git")
    assert [r["slug"] for r in baseline] == ["rule-alpha", "rule-beta"]

    # rule-beta gets two recent reads → it now outranks rule-alpha.
    now = datetime.now(timezone.utc)
    _write_log(tmp_vault, [
        {"tool": "read_mnemo_rule", "timestamp": _ts(now, 1), "args": {"slug": "rule-beta"}},
        {"tool": "read_mnemo_rule", "timestamp": _ts(now, 2), "args": {"slug": "rule-beta"}},
    ])
    boosted = list_rules_by_topic(tmp_vault, "git")
    assert [r["slug"] for r in boosted] == ["rule-beta", "rule-alpha"]


def test_list_rules_by_topic_source_count_still_dominates_popularity(tmp_vault):
    """A higher source_count must beat any popularity advantage."""
    from tests.unit.test_mcp_tools import _write_page

    _write_page(
        tmp_vault, "feedback", "fresh-but-rare",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],  # source_count=1
    )
    _write_page(
        tmp_vault, "feedback", "broadly-supported",
        tags=["auto-promoted", "git"],
        sources=["bots/a/n.md", "bots/b/n.md", "bots/c/n.md"],  # source_count=3
    )
    now = datetime.now(timezone.utc)
    _write_log(tmp_vault, [
        {"tool": "read_mnemo_rule", "timestamp": _ts(now, 1),
         "args": {"slug": "fresh-but-rare"}}
        for _ in range(50)
    ])
    result = list_rules_by_topic(tmp_vault, "git")
    assert [r["slug"] for r in result] == ["broadly-supported", "fresh-but-rare"]
