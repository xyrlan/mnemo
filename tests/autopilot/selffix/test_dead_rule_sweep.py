"""Tests for dead_rule_sweep — detect + archive dead rules."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def _run_id(days_ago: int = 0) -> str:
    """Reproduce a real extraction ``run_id``.

    ``datetime.now().isoformat(timespec="seconds")`` — naive *local* time, no
    ``Z``. This is the literal format ``_render_page`` and
    ``_render_project_page`` write into ``extracted_at`` / ``promoted_at`` /
    ``extraction_run``.
    """
    return (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _make_rule(
    tmp_path: Path,
    name: str,
    created_days_ago: int | None = None,
    *,
    date_field: str = "extracted_at",
    subtype: str = "feedback",
    mtime_days_ago: int | None = None,
) -> Path:
    """Write a rule page.

    Defaults mirror what ``mnemo extract`` actually emits: an ``extracted_at``
    line holding a naive-local ``run_id``. Pass ``created_days_ago=None`` with
    ``date_field=""`` to get a page carrying **no** date field at all — the
    shape that every real vault page had while the age guard was broken.
    """
    shared = tmp_path / "shared" / subtype
    shared.mkdir(parents=True, exist_ok=True)
    if date_field and created_days_ago is not None:
        stamp = (
            _ts(created_days_ago)
            if date_field == "created_at"
            else _run_id(created_days_ago)
        )
        date_line = f"{date_field}: {stamp}\n"
    else:
        date_line = ""
    content = f"""---
type: {subtype}
tags:
  - test
sources: []
{date_line}---
{"x" * 60}
"""
    p = shared / f"{name}.md"
    p.write_text(content, encoding="utf-8")
    if mtime_days_ago is not None:
        ts = (datetime.now(timezone.utc) - timedelta(days=mtime_days_ago)).timestamp()
        os.utime(p, (ts, ts))
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
# detect_dead_rules — age guard must fail CLOSED
#
# Regression cover for the sweep that emptied live vaults: the guard read a
# ``created_at`` field no writer emits, so age was always unknown, and unknown
# age meant "archive". Unknown age must mean "preserve".
# ---------------------------------------------------------------------------


def test_detect_dead_rules_preserves_page_with_no_date_field(tmp_path: Path) -> None:
    """THE BUG. A page carrying no date field at all must not be archived.

    Every page in the maintainer's 113-rule vault had this shape. The old
    guard (``if created is not None and created > cutoff``) never fired for
    any of them, so a rule created minutes ago was swept as dead.
    """
    _make_rule(tmp_path, "undated-rule", created_days_ago=None, date_field="")
    rules = detect_dead_rules(vault_root=tmp_path, days=90)
    assert rules == []


def test_detect_dead_rules_preserves_when_age_undeterminable(tmp_path: Path) -> None:
    """Even with mtime unavailable, an unknown-age page is preserved.

    Pins the ``created is None`` branch specifically: with the mtime fallback
    in place, a plain undated page is saved by its (recent) mtime, so that
    path alone would not prove the guard fails closed.
    """
    _make_rule(tmp_path, "unstattable", created_days_ago=None, date_field="")
    real_stat = Path.stat

    def boom(self, *a, **kw):
        if self.name == "unstattable.md":
            raise OSError("stat unavailable")
        return real_stat(self, *a, **kw)

    with patch.object(Path, "stat", boom):
        rules = detect_dead_rules(vault_root=tmp_path, days=90)
    assert rules == []


def test_detect_dead_rules_preserves_recent_extracted_at(tmp_path: Path) -> None:
    """A page whose only date field is recent is not archived."""
    _make_rule(tmp_path, "fresh", created_days_ago=10, date_field="extracted_at")
    rules = detect_dead_rules(vault_root=tmp_path, days=90)
    assert rules == []


def test_detect_dead_rules_archives_old_extracted_at(tmp_path: Path) -> None:
    """A page whose only date field is beyond the window IS archived.

    The sweep still has to work — a guard that never archives is as wrong as
    one that always does.
    """
    _make_rule(tmp_path, "stale", created_days_ago=200, date_field="extracted_at")
    rules = detect_dead_rules(vault_root=tmp_path, days=90)
    assert [r.slug for r in rules] == ["stale"]


@pytest.mark.parametrize("field", ["created_at", "promoted_at", "extracted_at", "extraction_run"])
def test_detect_dead_rules_reads_every_supported_age_field(
    tmp_path: Path, field: str
) -> None:
    """Each supported field can independently establish age, both ways."""
    _make_rule(tmp_path, "recent", created_days_ago=5, date_field=field)
    assert detect_dead_rules(vault_root=tmp_path, days=90) == []

    (tmp_path / "shared" / "feedback" / "recent.md").unlink()
    _make_rule(tmp_path, "ancient", created_days_ago=400, date_field=field)
    assert [r.slug for r in detect_dead_rules(vault_root=tmp_path, days=90)] == ["ancient"]


def test_detect_dead_rules_parses_naive_local_run_id(tmp_path: Path) -> None:
    """``extracted_at`` holds a naive-local ``run_id``, not a ``Z`` timestamp.

    ``_parse_ts`` only knew ``...Z`` and ``%Y-%m-%d`` formats, so even reading
    the right field would have yielded ``None`` for every real page.
    """
    from mnemo.autopilot.selffix.dead_rule_sweep import _parse_ts

    parsed = _parse_ts("2026-08-01T20:31:45")
    assert parsed is not None
    assert parsed.tzinfo is not None  # naive input must come back aware
    assert (parsed.year, parsed.month, parsed.day) == (2026, 8, 1)


def test_detect_dead_rules_ignores_date_in_body(tmp_path: Path) -> None:
    """A body line mimicking a date field must not decide age.

    Prose quoting an old ``extracted_at:`` would otherwise age a live page
    into the archive.
    """
    shared = tmp_path / "shared" / "feedback"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "prose.md").write_text(
        "---\ntype: feedback\ntags:\n  - test\nsources: []\n---\n"
        f"extracted_at: {_run_id(900)}\n" + "x" * 60 + "\n",
        encoding="utf-8",
    )
    # mtime is now, no frontmatter date → young → preserved.
    assert detect_dead_rules(vault_root=tmp_path, days=90) == []


# ---------------------------------------------------------------------------
# mtime fallback
# ---------------------------------------------------------------------------


def test_detect_dead_rules_mtime_fallback_archives_old_undated_page(
    tmp_path: Path,
) -> None:
    """No parseable field → fall back to mtime, and an old mtime means dead."""
    _make_rule(
        tmp_path, "ancient-undated", created_days_ago=None, date_field="",
        mtime_days_ago=400,
    )
    rules = detect_dead_rules(vault_root=tmp_path, days=90)
    assert [r.slug for r in rules] == ["ancient-undated"]


def test_detect_dead_rules_mtime_fallback_preserves_recent_undated_page(
    tmp_path: Path,
) -> None:
    _make_rule(
        tmp_path, "new-undated", created_days_ago=None, date_field="",
        mtime_days_ago=3,
    )
    assert detect_dead_rules(vault_root=tmp_path, days=90) == []


def test_detect_dead_rules_unparseable_field_falls_through_to_mtime(
    tmp_path: Path,
) -> None:
    """A garbage date value must not be read as 'unknown, archive anyway'."""
    shared = tmp_path / "shared" / "feedback"
    shared.mkdir(parents=True, exist_ok=True)
    p = shared / "garbled.md"
    p.write_text(
        "---\ntype: feedback\nextracted_at: not-a-date\nsources: []\n---\n"
        + "x" * 60 + "\n",
        encoding="utf-8",
    )
    ts = (datetime.now(timezone.utc) - timedelta(days=400)).timestamp()
    os.utime(p, (ts, ts))
    assert [r.slug for r in detect_dead_rules(vault_root=tmp_path, days=90)] == ["garbled"]


def test_detect_dead_rules_usage_signal_beats_age(tmp_path: Path) -> None:
    """A rule with a usage signal is never archived, however old it looks."""
    _make_rule(
        tmp_path, "ancient-but-used", created_days_ago=5000,
        date_field="extracted_at", mtime_days_ago=5000,
    )
    _write_reflex_log(tmp_path, [{"ts": _ts(2), "emitted": ["ancient-but-used"]}])
    assert detect_dead_rules(vault_root=tmp_path, days=90) == []


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
                    "last_changed_at": None, "last_changed_by": None}), 
    encoding="utf-8")
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
                    "last_changed_at": None, "last_changed_by": None}), 
    encoding="utf-8")
    with patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.create_worktree",
               return_value=tmp_path / "wt"), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.mirror_paths"), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.commit_all", return_value=True), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.remove_worktree"), \
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
                    "last_changed_at": None, "last_changed_by": None}), 
    encoding="utf-8")
    with patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.create_worktree",
               return_value=tmp_path / "wt"), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.mirror_paths"), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.commit_all", return_value=True), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.remove_worktree"), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.push_branch", return_value=True), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.open_pr", return_value=99), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep.pr_budget.record_opened"), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep._run_pytest", return_value=True):
        open_dead_rule_pr(dead, vault_root=tmp_path, repo_root=tmp_path)
    archive_dir = tmp_path / "shared" / "_archive"
    archived = list(archive_dir.glob("*.md"))
    assert len(archived) == MAX_RULES_PER_SWEEP_PR


# ---------------------------------------------------------------------------
# _run_pytest interpreter
# ---------------------------------------------------------------------------


def test_run_pytest_uses_sys_executable(tmp_path: Path) -> None:
    """Gate must spawn pytest via sys.executable, not bare "python".

    On macOS there is often no `python` on PATH (only `python3`), so a bare
    "python" argv raises FileNotFoundError and the gate reports a false
    "pytest failed" — blocking every sweep PR.
    """
    import sys
    from mnemo.autopilot.selffix import dead_rule_sweep

    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return MagicMock(returncode=0)

    with patch("mnemo.autopilot.selffix.dead_rule_sweep.subprocess.run", side_effect=fake_run):
        assert dead_rule_sweep._run_pytest(repo_root=tmp_path) is True
    assert captured["argv"][0] == sys.executable
