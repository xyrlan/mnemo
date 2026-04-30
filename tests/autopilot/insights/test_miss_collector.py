from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.autopilot.insights.miss_collector import collect_recall_misses
from mnemo.autopilot.core.proposals import list_proposals


_MNemo = ".mnemo"


def _write_recall_report(tmp_path: Path, results: list) -> None:
    data = {
        "generated_at": "2026-04-30T10:00:00Z",
        "report": {"cases": len(results), "misses": []},
        "results": results,
    }
    path = tmp_path / _MNemo / "recall-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _make_result(slug: str, hit: bool, project: str = "p") -> dict:
    return {
        "id": f"case-{slug}",
        "hit": hit,
        "rank": 1 if hit else None,
        "expect_slug": slug,
        "topic": "topic-foo",
        "project": project,
        "result_count": 5,
        "elapsed_ms": 1.5,
    }


# ── No recall-report → 0 ─────────────────────────────────────────────────────

def test_collect_no_recall_report(tmp_path: Path):
    count = collect_recall_misses(vault_root=tmp_path)
    assert count == 0
    assert list_proposals(vault_root=tmp_path) == []


# ── 2 misses → 2 proposals ───────────────────────────────────────────────────

def test_collect_writes_proposal_per_miss(tmp_path: Path):
    _write_recall_report(tmp_path, [
        _make_result("slug-a", hit=False),
        _make_result("slug-b", hit=False),
        _make_result("slug-c", hit=True),
    ])
    count = collect_recall_misses(vault_root=tmp_path)
    assert count == 2
    proposals = list_proposals(vault_root=tmp_path)
    assert len(proposals) == 2
    slugs = {p.payload["expected_slug"] for p in proposals}
    assert slugs == {"slug-a", "slug-b"}


# ── Only misses (hit==False) counted ─────────────────────────────────────────

def test_collect_skips_hits(tmp_path: Path):
    _write_recall_report(tmp_path, [
        _make_result("slug-x", hit=True),
    ])
    count = collect_recall_misses(vault_root=tmp_path)
    assert count == 0


# ── Idempotency ───────────────────────────────────────────────────────────────

def test_collect_idempotent(tmp_path: Path):
    _write_recall_report(tmp_path, [
        _make_result("slug-dup", hit=False),
    ])
    first = collect_recall_misses(vault_root=tmp_path)
    assert first == 1
    second = collect_recall_misses(vault_root=tmp_path)
    assert second == 0  # already pending
    proposals = list_proposals(vault_root=tmp_path, status="pending")
    assert len(proposals) == 1


# ── Payload fields are correct ────────────────────────────────────────────────

def test_collect_proposal_payload_fields(tmp_path: Path):
    _write_recall_report(tmp_path, [
        _make_result("my-rule", hit=False, project="proj1"),
    ])
    collect_recall_misses(vault_root=tmp_path)
    proposals = list_proposals(vault_root=tmp_path)
    assert len(proposals) == 1
    p = proposals[0]
    assert p.kind == "rule_candidate"
    assert p.source == "tier0.miss_collector"
    assert p.project == "proj1"
    assert p.confidence == 0.0
    payload = p.payload
    assert payload["expected_slug"] == "my-rule"
    assert payload["topic"] == "topic-foo"
    assert "miss in recall" in payload["reason"]
    assert "recall_report_at" in payload


# ── Cross-project: same slug different project → both written ─────────────────

def test_collect_same_slug_different_projects(tmp_path: Path):
    _write_recall_report(tmp_path, [
        _make_result("shared-slug", hit=False, project="proj-a"),
        _make_result("shared-slug", hit=False, project="proj-b"),
    ])
    count = collect_recall_misses(vault_root=tmp_path)
    assert count == 2
