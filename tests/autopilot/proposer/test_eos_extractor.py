"""Tests for autopilot/proposer/eos_extractor.py"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mnemo.autopilot.proposer.eos_extractor import (
    RuleCandidate,
    _compute_confidence,
    _is_duplicate,
    _load_vault_slugs,
    _make_slug_hint,
    analyze_session,
)


# --- _compute_confidence ---

def test_confidence_zero_baseline():
    score = _compute_confidence(
        pattern_count=1, session_count=1,
        has_denial=False, has_always_keyword=False,
    )
    assert score == 0.0


def test_confidence_repeated_pattern():
    score = _compute_confidence(
        pattern_count=2, session_count=1,
        has_denial=False, has_always_keyword=False,
    )
    assert score == pytest.approx(0.3)


def test_confidence_multi_session():
    score = _compute_confidence(
        pattern_count=1, session_count=2,
        has_denial=False, has_always_keyword=False,
    )
    assert score == pytest.approx(0.3)


def test_confidence_denial():
    score = _compute_confidence(
        pattern_count=1, session_count=1,
        has_denial=True, has_always_keyword=False,
    )
    assert score == pytest.approx(0.2)


def test_confidence_always_keyword():
    score = _compute_confidence(
        pattern_count=1, session_count=1,
        has_denial=False, has_always_keyword=True,
    )
    assert score == pytest.approx(0.2)


def test_confidence_all_signals():
    score = _compute_confidence(
        pattern_count=3, session_count=3,
        has_denial=True, has_always_keyword=True,
    )
    assert score == pytest.approx(1.0)


def test_confidence_capped_at_one():
    score = _compute_confidence(
        pattern_count=99, session_count=99,
        has_denial=True, has_always_keyword=True,
    )
    assert score == 1.0


def test_confidence_repeated_and_multi():
    score = _compute_confidence(
        pattern_count=2, session_count=2,
        has_denial=False, has_always_keyword=False,
    )
    assert score == pytest.approx(0.6)


# --- _make_slug_hint ---

def test_make_slug_hint_basic():
    assert _make_slug_hint("normalize price") == "normalize-price"


def test_make_slug_hint_strips_special_chars():
    assert _make_slug_hint("fix: typo!") == "fix-typo"


def test_make_slug_hint_truncates():
    long = "a" * 100
    hint = _make_slug_hint(long)
    assert len(hint) <= 60


# --- _load_vault_slugs ---

def test_load_vault_slugs_finds_slugs(tmp_path: Path):
    shared = tmp_path / "shared" / "rules"
    shared.mkdir(parents=True)
    (shared / "rule1.md").write_text("---\nslug: fix-nan-price\ntitle: Fix NaN\n---\nbody")
    (shared / "rule2.md").write_text("---\nslug: validate-input\n---\nbody")
    slugs = _load_vault_slugs(tmp_path)
    assert "fix-nan-price" in slugs
    assert "validate-input" in slugs


def test_load_vault_slugs_empty_when_no_shared(tmp_path: Path):
    slugs = _load_vault_slugs(tmp_path)
    assert slugs == set()


# --- _is_duplicate ---

def test_is_duplicate_exact_match():
    assert _is_duplicate("fix-nan", {"fix-nan", "other"}) is True


def test_is_duplicate_prefix_match():
    assert _is_duplicate("fix-nan", {"fix-nan-normalization"}) is True


def test_is_duplicate_reverse_prefix():
    assert _is_duplicate("fix-nan-normalization", {"fix-nan"}) is True


def test_is_duplicate_no_match():
    assert _is_duplicate("validate-input", {"fix-nan", "parse-config"}) is False


def test_is_duplicate_empty_slugs():
    assert _is_duplicate("anything", set()) is False


# --- analyze_session ---

def test_analyze_session_no_signals_returns_empty(tmp_path: Path):
    with patch(
        "mnemo.autopilot.proposer.eos_extractor.git_log_since",
        return_value=[],
    ):
        candidates = analyze_session(
            session_id="sess-001",
            project="test-project",
            vault_root=tmp_path,
            cwd=tmp_path,
        )
    assert candidates == []


def test_analyze_session_repeated_pattern_creates_candidate(tmp_path: Path):
    messages = [
        "normalize price value",
        "normalize price format",
        "normalize price input",
    ]
    with patch(
        "mnemo.autopilot.proposer.eos_extractor.git_log_since",
        return_value=messages,
    ):
        candidates = analyze_session(
            session_id="sess-001",
            project="test-project",
            vault_root=tmp_path,
            cwd=tmp_path,
        )
    assert len(candidates) == 1
    assert candidates[0].slug_hint == "normalize-price"
    assert candidates[0].confidence > 0.0
    assert isinstance(candidates[0], RuleCandidate)


def test_analyze_session_writes_proposal(tmp_path: Path):
    messages = [
        "normalize price value",
        "normalize price format",
    ]
    with patch(
        "mnemo.autopilot.proposer.eos_extractor.git_log_since",
        return_value=messages,
    ):
        candidates = analyze_session(
            session_id="sess-001",
            project="test-project",
            vault_root=tmp_path,
            cwd=tmp_path,
        )
    assert len(candidates) == 1
    # Proposal should be on disk
    proposals_dir = tmp_path / ".mnemo" / "proposals"
    assert proposals_dir.exists()
    files = list(proposals_dir.iterdir())
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["kind"] == "rule_candidate"
    assert data["source"] == "tier3.eos_extractor"


def test_analyze_session_skips_duplicates(tmp_path: Path):
    # Pre-populate vault with existing rule
    shared = tmp_path / "shared" / "rules"
    shared.mkdir(parents=True)
    (shared / "normalize-price.md").write_text("---\nslug: normalize-price\n---\nbody")

    messages = [
        "normalize price value",
        "normalize price format",
    ]
    with patch(
        "mnemo.autopilot.proposer.eos_extractor.git_log_since",
        return_value=messages,
    ):
        candidates = analyze_session(
            session_id="sess-001",
            project="test-project",
            vault_root=tmp_path,
            cwd=tmp_path,
        )
    assert candidates == []


def test_analyze_session_denial_increases_confidence(tmp_path: Path):
    # Write a denial log entry
    mnemo_dir = tmp_path / ".mnemo"
    mnemo_dir.mkdir()
    denial_log = mnemo_dir / "denial-log.jsonl"
    denial_log.write_text(
        json.dumps({"session_id": "sess-denial", "rule": "some-rule", "ts": "2026-01-01"})
        + "\n"
    )

    messages = [
        "normalize price value",
        "normalize price format",
    ]
    with patch(
        "mnemo.autopilot.proposer.eos_extractor.git_log_since",
        return_value=messages,
    ):
        candidates = analyze_session(
            session_id="sess-denial",
            project="test-project",
            vault_root=tmp_path,
            cwd=tmp_path,
        )
    assert len(candidates) == 1
    # +0.3 (repeated) + 0.2 (denial) = 0.5
    assert candidates[0].confidence == pytest.approx(0.5)


def test_analyze_session_high_confidence_writes_stub(tmp_path: Path):
    """Confidence >= 0.9 AND sessions >= 2 should write a rule stub."""
    messages = [
        "normalize price value",
        "normalize price format",
    ]
    # Patch to simulate Tier 0 proposals (multi-session signal)
    with patch(
        "mnemo.autopilot.proposer.eos_extractor.git_log_since",
        return_value=messages,
    ), patch(
        "mnemo.autopilot.proposer.eos_extractor._read_tier0_proposals",
        return_value=[{"slug_hint": "normalize-price", "source": "tier0.miss"}],
    ), patch(
        "mnemo.autopilot.proposer.eos_extractor._read_denial_log",
        return_value=[{"session_id": "sess-high", "rule": "x"}],
    ), patch(
        "mnemo.autopilot.proposer.eos_extractor.scan_for_keywords",
        return_value=True,
    ):
        candidates = analyze_session(
            session_id="sess-high",
            project="test-project",
            vault_root=tmp_path,
            cwd=tmp_path,
        )
    assert len(candidates) == 1
    assert candidates[0].confidence == 1.0
    # Rule stub should exist
    inbox = tmp_path / "shared" / "_inbox" / "reference"
    stubs = list(inbox.glob("*.md"))
    assert len(stubs) == 1
