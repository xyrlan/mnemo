from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mnemo.autopilot.insights.digest import (
    DigestData,
    generate_digest,
    render_digest_markdown,
    write_digest,
)


_MNemo = ".mnemo"


def _iso_dt(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _write_recall_report(tmp_path: Path, *, misses: list, hits: list) -> None:
    results = []
    for s in hits:
        results.append({
            "id": f"hit-{s}", "hit": True, "rank": 1, "expect_slug": s,
            "topic": "foo", "project": "p", "result_count": 5, "elapsed_ms": 1.0,
        })
    for s in misses:
        results.append({
            "id": f"miss-{s}", "hit": False, "rank": None, "expect_slug": s,
            "topic": "bar", "project": "p", "result_count": 5, "elapsed_ms": 2.0,
        })
    total = len(results)
    hit_count = len(hits)
    data = {
        "generated_at": "2026-04-30T10:00:00Z",
        "report": {
            "primacy_rate_at_5": hit_count / total if total else 0.0,
            "mrr": 0.55,
            "p95_latency_ms": 3.0,
            "cases": total,
            "misses": [f"miss-{s}" for s in misses],
            "primacy_at_5": hit_count,
            "primacy_at_3": hit_count,
            "primacy_at_10": hit_count,
            "primacy_rate_at_3": hit_count / total if total else 0.0,
            "primacy_rate_at_10": hit_count / total if total else 0.0,
        },
        "results": results,
    }
    path = tmp_path / _MNemo / "recall-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _write_reflex_log(tmp_path: Path, entries: list) -> None:
    path = tmp_path / _MNemo / "reflex-log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def _write_denial_log(tmp_path: Path, entries: list) -> None:
    path = tmp_path / _MNemo / "denial-log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def _write_mcp_log(tmp_path: Path, entries: list) -> None:
    path = tmp_path / _MNemo / "mcp-access-log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


# ── DigestData basic shape ────────────────────────────────────────────────────

def test_generate_digest_empty_vault(tmp_path: Path):
    d = generate_digest(vault_root=tmp_path, since_days=7)
    assert isinstance(d, DigestData)
    # No log files — everything is zero/None
    assert d.reflex_prompt_count == 0
    assert d.denial_count == 0
    assert d.recall_primacy_at_5 is None


# ── Reflex section ────────────────────────────────────────────────────────────

def test_generate_digest_reflex_counts(tmp_path: Path):
    entries = [
        {"ts": "2026-04-25T10:00:00Z", "emitted": True, "session_id": "s1",
         "silence_reason": None},
        {"ts": "2026-04-25T10:01:00Z", "emitted": False, "session_id": "s2",
         "silence_reason": "relative_gap_fail"},
        {"ts": "2026-04-25T10:02:00Z", "emitted": False, "session_id": "s3",
         "silence_reason": "below_min_tokens"},
        {"ts": "2026-04-25T10:03:00Z", "emitted": False, "session_id": "s4",
         "silence_reason": "relative_gap_fail"},
    ]
    _write_reflex_log(tmp_path, entries)
    d = generate_digest(vault_root=tmp_path, since_days=365)
    assert d.reflex_prompt_count == 4
    # 1 emitted → 1/4 = 25%
    assert abs(d.reflex_emit_rate - 0.25) < 0.001
    # top silence reason: relative_gap_fail (2)
    assert d.reflex_top_silence_reasons[0][0] == "relative_gap_fail"
    assert d.reflex_top_silence_reasons[0][1] == 2


def test_generate_digest_reflex_index_missing_count(tmp_path: Path):
    entries = [
        {"ts": "2026-04-25T10:00:00Z", "emitted": False,
         "silence_reason": "index_missing", "session_id": "s1"},
        {"ts": "2026-04-25T10:01:00Z", "emitted": False,
         "silence_reason": "index_missing", "session_id": "s2"},
        {"ts": "2026-04-25T10:02:00Z", "emitted": True,
         "silence_reason": None, "session_id": "s3"},
    ]
    _write_reflex_log(tmp_path, entries)
    d = generate_digest(vault_root=tmp_path, since_days=365)
    assert d.reflex_index_missing_count == 2


# ── Denials section ───────────────────────────────────────────────────────────

def test_generate_digest_denials(tmp_path: Path):
    entries = [
        {"timestamp": "2026-04-25T10:00:00Z", "slug": "supabase-single-db",
         "project": "p", "reason": "enforce block"},
        {"timestamp": "2026-04-25T10:01:00Z", "slug": "supabase-single-db",
         "project": "p", "reason": "enforce block"},
        {"timestamp": "2026-04-25T10:02:00Z", "slug": "other-rule",
         "project": "p", "reason": "enforce block"},
    ]
    _write_denial_log(tmp_path, entries)
    d = generate_digest(vault_root=tmp_path, since_days=365)
    assert d.denial_count == 3
    assert d.top_denial_slug == "supabase-single-db"
    assert d.top_denial_count == 2


# ── Recall section ────────────────────────────────────────────────────────────

def test_generate_digest_recall_section(tmp_path: Path):
    _write_recall_report(tmp_path, hits=["rule-a", "rule-b", "rule-c"], misses=["rule-d"])
    d = generate_digest(vault_root=tmp_path, since_days=7)
    assert d.recall_primacy_at_5 is not None
    assert abs(d.recall_primacy_at_5 - 0.75) < 0.001
    assert d.recall_mrr is not None
    assert d.recall_p95_ms is not None


# ── Top emitted rules ─────────────────────────────────────────────────────────

def test_generate_digest_top_emitted(tmp_path: Path):
    entries = [
        {"timestamp": "2026-04-25T10:00:00Z", "tool": "read_mnemo_rule",
         "args": {"slug": "canonical-workflow"}, "project": "p"},
        {"timestamp": "2026-04-25T10:01:00Z", "tool": "read_mnemo_rule",
         "args": {"slug": "canonical-workflow"}, "project": "p"},
        {"timestamp": "2026-04-25T10:02:00Z", "tool": "read_mnemo_rule",
         "args": {"slug": "solo-dev-auto-mode"}, "project": "p"},
        {"timestamp": "2026-04-25T10:03:00Z", "tool": "list_rules_by_topic",
         "args": {"topic": "workflow"}, "project": "p"},
    ]
    _write_mcp_log(tmp_path, entries)
    d = generate_digest(vault_root=tmp_path, since_days=365)
    assert len(d.top_emitted_rules) >= 1
    assert d.top_emitted_rules[0][0] == "canonical-workflow"
    assert d.top_emitted_rules[0][1] == 2


# ── render_digest_markdown ───────────────────────────────────────────────────

def test_render_digest_markdown_sections(tmp_path: Path):
    _write_recall_report(tmp_path, hits=["rule-a"], misses=["rule-b"])
    reflex_entries = [
        {"ts": "2026-04-25T10:00:00Z", "emitted": True, "session_id": "s1",
         "silence_reason": None},
        {"ts": "2026-04-25T10:01:00Z", "emitted": False, "session_id": "s2",
         "silence_reason": "relative_gap_fail"},
    ]
    _write_reflex_log(tmp_path, reflex_entries)
    denial_entries = [
        {"timestamp": "2026-04-25T10:00:00Z", "slug": "bad-rule", "project": "p",
         "reason": "x"},
    ]
    _write_denial_log(tmp_path, denial_entries)
    d = generate_digest(vault_root=tmp_path, since_days=7)
    md = render_digest_markdown(d, date_str="2026-04-30")

    assert "# Autopilot weekly digest" in md
    assert "2026-04-30" in md
    assert "## Recall" in md
    assert "## Reflex" in md
    assert "## Denials" in md
    assert "## Top emitted rules" in md


def test_render_digest_markdown_numbers(tmp_path: Path):
    _write_recall_report(tmp_path, hits=["rule-a", "rule-b"], misses=["rule-c"])
    _write_reflex_log(tmp_path, [
        {"ts": "2026-04-25T10:00:00Z", "emitted": True, "session_id": "s1",
         "silence_reason": None},
    ])
    d = generate_digest(vault_root=tmp_path, since_days=7)
    md = render_digest_markdown(d, date_str="2026-04-30")
    # Primacy should appear: 2 hits out of 3 = 66.7%
    assert "66.7%" in md


# ── write_digest ─────────────────────────────────────────────────────────────

def test_write_digest_creates_file(tmp_path: Path):
    d = generate_digest(vault_root=tmp_path, since_days=7)
    path = write_digest(vault_root=tmp_path, digest=d)
    assert path.exists()
    assert "autopilot" in str(path)
    assert path.suffix == ".md"
    content = path.read_text()
    assert "# Autopilot weekly digest" in content


def test_write_digest_path_format(tmp_path: Path):
    d = generate_digest(vault_root=tmp_path, since_days=7)
    path = write_digest(vault_root=tmp_path, digest=d)
    # Should be under briefings/autopilot/
    assert "briefings" in path.parts
    assert "autopilot" in path.parts
    assert path.name.endswith("-digest.md")


# ── post_digest_issue ─────────────────────────────────────────────────────────

def test_post_digest_issue_returns_number_on_success(tmp_path: Path):
    from mnemo.autopilot.insights.digest import post_digest_issue

    d = generate_digest(vault_root=tmp_path, since_days=7)

    def fake_run(cmd, capture_output, text):
        class R:
            returncode = 0
            stdout = "https://github.com/owner/repo/issues/42\n"
            stderr = ""
        return R()

    result = post_digest_issue(digest=d, _run=fake_run)
    assert result == 42


def test_post_digest_issue_returns_none_on_failure(tmp_path: Path):
    from mnemo.autopilot.insights.digest import post_digest_issue

    d = generate_digest(vault_root=tmp_path, since_days=7)

    def fake_run(cmd, capture_output, text):
        class R:
            returncode = 1
            stdout = ""
            stderr = "error"
        return R()

    result = post_digest_issue(digest=d, _run=fake_run)
    assert result is None


def test_post_digest_issue_returns_none_when_gh_missing(tmp_path: Path):
    from mnemo.autopilot.insights.digest import post_digest_issue

    d = generate_digest(vault_root=tmp_path, since_days=7)

    def fake_run(cmd, capture_output, text):
        raise FileNotFoundError("gh not found")

    result = post_digest_issue(digest=d, _run=fake_run)
    assert result is None
