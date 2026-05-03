"""Tests for dead_rule_sweep — detect + archive dead rules."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from mnemo.autopilot.selffix.dead_rule_sweep import (
    DEFAULT_DEAD_WINDOW_DAYS,
    MAX_RULES_PER_SWEEP_PR,
    DeadRule,
    archive_rule,
    detect_dead_rules,
    open_dead_rule_pr,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(days_ago: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_rule(
    tmp_path: Path,
    name: str,
    created_days_ago: int = 100,
) -> Path:
    shared = tmp_path / "shared" / "feedback"
    shared.mkdir(parents=True, exist_ok=True)
    content = f"""---
type: feedback
tags:
  - test
sources: []
created_at: {_ts(created_days_ago)}
---
{"x" * 60}
"""
    p = shared / f"{name}.md"
    p.write_text(content, encoding="utf-8")
    return p


def _write_access_log(tmp_path: Path, entries: list) -> None:
    log_path = tmp_path / ".mnemo" / "mcp-access-log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _write_reflex_log(tmp_path: Path, entries: list) -> None:
    log_path = tmp_path / ".mnemo" / "reflex-log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


# ---------------------------------------------------------------------------
# DeadRule dataclass
# ---------------------------------------------------------------------------


def test_dead_rule_has_required_fields(tmp_path: Path) -> None:
    p = tmp_path / "shared" / "feedback" / "r.md"
    dr = DeadRule(rule_path=p, slug="r", last_seen_days=91)
    assert dr.rule_path == p
    assert dr.slug == "r"
    assert dr.last_seen_days == 91


# ---------------------------------------------------------------------------
# detect_dead_rules — no activity logs
# ---------------------------------------------------------------------------


def test_detect_dead_rules_returns_old_rule_with_no_activity(tmp_path: Path) -> None:
    _make_rule(tmp_path, "old-rule", created_days_ago=100)
    rules = detect_dead_rules(vault_root=tmp_path, days=90)
    assert len(rules) == 1
    assert rules[0].slug == "old-rule"


def test_detect_dead_rules_skips_recently_created(tmp_path: Path) -> None:
    """A rule created 10 days ago is not dead even with 0 hits."""
    _make_rule(tmp_path, "new-rule", created_days_ago=10)
    rules = detect_dead_rules(vault_root=tmp_path, days=90)
    assert rules == []


def test_detect_dead_rules_skips_no_shared_dir(tmp_path: Path) -> None:
    rules = detect_dead_rules(vault_root=tmp_path, days=90)
    assert rules == []


# ---------------------------------------------------------------------------
# detect_dead_rules — with activity in access log
# ---------------------------------------------------------------------------


def test_detect_dead_rules_skips_recently_accessed(tmp_path: Path) -> None:
    """A rule that was hit 30 days ago (within window) should be skipped."""
    _make_rule(tmp_path, "active-rule", created_days_ago=100)
    _write_access_log(tmp_path, [
        {
            "ts": _ts(30),
            "rules": [{"slug": "active-rule"}],
        }
    ])
    rules = detect_dead_rules(vault_root=tmp_path, days=90)
    assert rules == []


def test_detect_dead_rules_counts_ancient_access_as_dead(tmp_path: Path) -> None:
    """An access that happened 100 days ago (outside 90-day window) doesn't save the rule."""
    _make_rule(tmp_path, "old-active-rule", created_days_ago=200)
    _write_access_log(tmp_path, [
        {
            "ts": _ts(100),
            "rules": [{"slug": "old-active-rule"}],
        }
    ])
    rules = detect_dead_rules(vault_root=tmp_path, days=90)
    assert any(r.slug == "old-active-rule" for r in rules)


def test_detect_dead_rules_skips_rule_in_reflex_log(tmp_path: Path) -> None:
    _make_rule(tmp_path, "reflex-rule", created_days_ago=100)
    _write_reflex_log(tmp_path, [
        {
            "ts": _ts(10),
            "emitted": ["reflex-rule"],
        }
    ])
    rules = detect_dead_rules(vault_root=tmp_path, days=90)
    assert rules == []


# ---------------------------------------------------------------------------
# archive_rule
# ---------------------------------------------------------------------------


def test_archive_rule_moves_file(tmp_path: Path) -> None:
    rule = _make_rule(tmp_path, "old-rule")
    archived = archive_rule(rule, vault_root=tmp_path)
    assert archived.parent == tmp_path / "shared" / "_archive"
    assert archived.exists()
    assert not rule.exists()


def test_archive_rule_creates_archive_dir(tmp_path: Path) -> None:
    rule = _make_rule(tmp_path, "old-rule")
    archive_dir = tmp_path / "shared" / "_archive"
    assert not archive_dir.exists()
    archive_rule(rule, vault_root=tmp_path)
    assert archive_dir.is_dir()


def test_archive_rule_path_within_perimeter(tmp_path: Path) -> None:
    from mnemo.autopilot.selffix._perimeter import is_within_perimeter
    rule = _make_rule(tmp_path, "old-rule")
    archived = archive_rule(rule, vault_root=tmp_path)
    # archived path is under shared/_archive → should be within perimeter
    assert is_within_perimeter(archived, repo_root=tmp_path)


# ---------------------------------------------------------------------------
# open_dead_rule_pr
# ---------------------------------------------------------------------------


def test_open_dead_rule_pr_dry_run_no_pr(tmp_path: Path) -> None:
    rule = _make_rule(tmp_path, "old-rule")
    dead = [DeadRule(rule_path=rule, slug="old-rule", last_seen_days=100)]
    (tmp_path / ".mnemo").mkdir(exist_ok=True)
    (tmp_path / ".mnemo" / "autopilot.json").write_text(
        json.dumps({"schema_version": 1, "state": "on", "paused_until": None,
                    "last_changed_at": None, "last_changed_by": None})
    )
    with patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.open_pr") as mock_pr:
        result = open_dead_rule_pr(dead, vault_root=tmp_path, repo_root=tmp_path, dry_run=True)
    mock_pr.assert_not_called()
    assert result is None


def test_open_dead_rule_pr_skips_when_budget_exhausted(tmp_path: Path) -> None:
    rule = _make_rule(tmp_path, "old-rule")
    dead = [DeadRule(rule_path=rule, slug="old-rule", last_seen_days=100)]
    with patch(
        "mnemo.autopilot.selffix.dead_rule_sweep.pr_budget.can_open",
        return_value=(False, "daily cap reached"),
    ):
        result = open_dead_rule_pr(dead, vault_root=tmp_path, repo_root=tmp_path)
    assert result is None


def test_open_dead_rule_pr_opens_pr_on_success(tmp_path: Path) -> None:
    rule = _make_rule(tmp_path, "old-rule")
    dead = [DeadRule(rule_path=rule, slug="old-rule", last_seen_days=100)]
    (tmp_path / ".mnemo").mkdir(exist_ok=True)
    (tmp_path / ".mnemo" / "autopilot.json").write_text(
        json.dumps({"schema_version": 1, "state": "on", "paused_until": None,
                    "last_changed_at": None, "last_changed_by": None})
    )
    with patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.create_branch", return_value="b"), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.push_branch", return_value=True), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.open_pr", return_value=55), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep.pr_budget.record_opened") as mock_rec, \
         patch("mnemo.autopilot.selffix.dead_rule_sweep._run_pytest", return_value=True):
        result = open_dead_rule_pr(dead, vault_root=tmp_path, repo_root=tmp_path)
    assert result == 55
    mock_rec.assert_called_once()


def test_default_dead_window_is_180_days() -> None:
    assert DEFAULT_DEAD_WINDOW_DAYS == 180


def test_max_rules_per_sweep_pr_is_50() -> None:
    assert MAX_RULES_PER_SWEEP_PR == 50


def test_detect_dead_rules_default_window_180_skips_120d_old(tmp_path: Path) -> None:
    """A rule active 120d ago is NOT dead under the new 180d default."""
    _make_rule(tmp_path, "old-but-active", created_days_ago=300)
    _write_access_log(tmp_path, [
        {"ts": _ts(120), "rules": [{"slug": "old-but-active"}]},
    ])
    rules = detect_dead_rules(vault_root=tmp_path)  # uses default
    assert rules == []


def test_open_dead_rule_pr_caps_at_max(tmp_path: Path) -> None:
    """When >MAX rules dead, the PR archives only MAX, leaves the rest."""
    dead = []
    for i in range(MAX_RULES_PER_SWEEP_PR + 5):
        rule = _make_rule(tmp_path, f"r{i}")
        dead.append(DeadRule(rule_path=rule, slug=f"r{i}", last_seen_days=200))
    (tmp_path / ".mnemo").mkdir(exist_ok=True)
    (tmp_path / ".mnemo" / "autopilot.json").write_text(
        json.dumps({"schema_version": 1, "state": "on", "paused_until": None,
                    "last_changed_at": None, "last_changed_by": None})
    )
    with patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.create_branch", return_value="b"), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.push_branch", return_value=True), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.open_pr", return_value=99), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep.pr_budget.record_opened"), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep._run_pytest", return_value=True):
        open_dead_rule_pr(dead, vault_root=tmp_path, repo_root=tmp_path)
    archive_dir = tmp_path / "shared" / "_archive"
    archived = list(archive_dir.glob("*.md"))
    assert len(archived) == MAX_RULES_PER_SWEEP_PR
