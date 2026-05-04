"""Tests for preempt-cache integration in session_start + session_end hooks.

Backwards compatibility is non-negotiable:
- When preempt-cache is missing/stale, _build_injection_payload must behave exactly as before.
- When preempt-cache exists and is valid, the payload includes a [predicted-rules] block.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mnemo.hooks.session_start import _build_injection_payload
from mnemo.hooks.session_end import _maybe_schedule_propose


# ------------------------------------------------------------------ #
# SessionStart preempt-cache integration                              #
# ------------------------------------------------------------------ #

def _fake_idx(vault_root: Path) -> dict:
    return {
        "rules": {
            "fix-nan": {
                "name": "Fix NaN",
                "topic_tags": ["nan", "price"],
                "source_count": 2,
                "universal": False,
            }
        },
        "by_project": {
            "test-proj": {"local_slugs": ["fix-nan"]},
        },
        "universal": {"slugs": []},
    }


def _write_fresh_cache(
    vault_root: Path,
    slugs: list[str],
    project: str = "test-proj",
    branch: str = "main",
    minutes_ago: int = 0,
) -> None:
    predicted_at = (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {
        "predicted_at": predicted_at,
        "project": project,
        "slugs": slugs,
        "ttl_minutes": 30,
        "branch": branch,
    }
    (vault_root / ".mnemo").mkdir(parents=True, exist_ok=True)
    (vault_root / ".mnemo" / "preempt-cache.json").write_text(json.dumps(data))


def _minimal_cfg_patch(vault_root: Path):
    """Return patch targets for a minimal config + rule_activation + mcp."""
    return [
        patch("mnemo.core.config.load_config", return_value={
            "injection": {"enabled": True, "maxTopicsPerScope": 5},
        }),
        patch("mnemo.core.rule_activation.load_index", return_value=None),
        patch(
            "mnemo.core.mcp.tools.get_mnemo_topics",
            return_value=["nan", "price"],
        ),
    ]


def test_session_start_no_cache_behaves_as_before(tmp_path: Path):
    """No preempt-cache → payload identical to pre-Tier3 output."""
    patches = _minimal_cfg_patch(tmp_path)
    with patches[0], patches[1], patches[2]:
        payload = _build_injection_payload(
            vault_root=tmp_path,
            current_project="test-proj",
            inject_briefing=False,
        )
    assert "[predicted-rules" not in payload
    # Still has topic list
    assert "nan" in payload or payload == ""


def test_session_start_stale_cache_no_predicted_block(tmp_path: Path):
    """Stale preempt-cache (> 30 min) → no [predicted-rules] block."""
    _write_fresh_cache(tmp_path, ["slug-x"], minutes_ago=40)
    patches = _minimal_cfg_patch(tmp_path)
    with patches[0], patches[1], patches[2], \
         patch("mnemo.autopilot.proposer.preempt.git_current_branch", return_value="main"):
        payload = _build_injection_payload(
            vault_root=tmp_path,
            current_project="test-proj",
            inject_briefing=False,
        )
    assert "[predicted-rules" not in payload


def test_session_start_valid_cache_adds_predicted_block(tmp_path: Path):
    """Fresh preempt-cache → [predicted-rules] block appended."""
    _write_fresh_cache(tmp_path, ["slug-a", "slug-b"], minutes_ago=5)
    patches = _minimal_cfg_patch(tmp_path)
    with patches[0], patches[1], patches[2], \
         patch("mnemo.autopilot.proposer.preempt.git_current_branch", return_value="main"):
        payload = _build_injection_payload(
            vault_root=tmp_path,
            current_project="test-proj",
            inject_briefing=False,
        )
    assert "[predicted-rules" in payload
    assert "slug-a" in payload
    assert "slug-b" in payload
    assert "[/predicted-rules]" in payload


def test_session_start_cache_wrong_project_no_block(tmp_path: Path):
    """Cache for different project → [predicted-rules] not added."""
    _write_fresh_cache(tmp_path, ["slug-x"], project="other-proj", minutes_ago=5)
    patches = _minimal_cfg_patch(tmp_path)
    with patches[0], patches[1], patches[2], \
         patch("mnemo.autopilot.proposer.preempt.git_current_branch", return_value="main"):
        payload = _build_injection_payload(
            vault_root=tmp_path,
            current_project="test-proj",
            inject_briefing=False,
        )
    assert "[predicted-rules" not in payload


def test_session_start_preempt_exception_swallowed(tmp_path: Path):
    """Exception in preempt read → must not propagate."""
    patches = _minimal_cfg_patch(tmp_path)
    with patches[0], patches[1], patches[2], \
         patch(
             "mnemo.autopilot.proposer.preempt.read_preempt_cache",
             side_effect=RuntimeError("boom"),
         ):
        # Must not raise
        payload = _build_injection_payload(
            vault_root=tmp_path,
            current_project="test-proj",
            inject_briefing=False,
        )
    assert "[predicted-rules" not in payload


def test_session_start_empty_slugs_no_block(tmp_path: Path):
    """Cache with empty slugs list → no block added."""
    _write_fresh_cache(tmp_path, [], minutes_ago=5)
    patches = _minimal_cfg_patch(tmp_path)
    with patches[0], patches[1], patches[2], \
         patch("mnemo.autopilot.proposer.preempt.git_current_branch", return_value="main"):
        payload = _build_injection_payload(
            vault_root=tmp_path,
            current_project="test-proj",
            inject_briefing=False,
        )
    assert "[predicted-rules" not in payload


# ------------------------------------------------------------------ #
# SessionEnd propose integration                                       #
# ------------------------------------------------------------------ #

def test_maybe_schedule_propose_called_when_enabled(tmp_path: Path):
    from mnemo.autopilot.core.kill_switch import set_state
    set_state(vault_root=tmp_path, state="on")
    cfg = {}
    with patch(
        "mnemo.autopilot.proposer.eos_extractor.analyze_session",
        return_value=[],
    ) as mock_analyze, patch(
        "mnemo.core.agent.resolve_canonical_agent",
        return_value=type("A", (), {"name": "test-proj"})(),
    ):
        _maybe_schedule_propose(
            cfg, tmp_path, "test-proj",
            session_id="sess-001", cwd=str(tmp_path),
        )
    mock_analyze.assert_called_once()
    call_kwargs = mock_analyze.call_args[1]
    assert call_kwargs["session_id"] == "sess-001"
    assert call_kwargs["project"] == "test-proj"


def test_maybe_schedule_propose_not_called_when_disabled(tmp_path: Path):
    # The function gates on kill_switch (`is_active`), not on cfg. With the
    # default flip to "on", we must explicitly disable to verify no-op.
    from mnemo.autopilot.core.kill_switch import set_state
    set_state(vault_root=tmp_path, state="off")
    cfg = {"autopilot": {"propose": {"enabled": False}}}
    with patch(
        "mnemo.autopilot.proposer.eos_extractor.analyze_session",
        return_value=[],
    ) as mock_analyze:
        _maybe_schedule_propose(
            cfg, tmp_path, "test-proj",
            session_id="sess-001", cwd=str(tmp_path),
        )
    mock_analyze.assert_not_called()


def test_maybe_schedule_propose_not_called_when_no_config(tmp_path: Path):
    from mnemo.autopilot.core.kill_switch import set_state
    set_state(vault_root=tmp_path, state="off")
    cfg = {}
    with patch(
        "mnemo.autopilot.proposer.eos_extractor.analyze_session",
        return_value=[],
    ) as mock_analyze:
        _maybe_schedule_propose(
            cfg, tmp_path, "test-proj",
            session_id="sess-001", cwd=str(tmp_path),
        )
    mock_analyze.assert_not_called()


def test_maybe_schedule_propose_swallows_exception(tmp_path: Path):
    cfg = {"autopilot": {"propose": {"enabled": True}}}
    with patch(
        "mnemo.autopilot.proposer.eos_extractor.analyze_session",
        side_effect=RuntimeError("boom"),
    ), patch(
        "mnemo.core.agent.resolve_canonical_agent",
        return_value=type("A", (), {"name": "test-proj"})(),
    ):
        # Must not raise
        _maybe_schedule_propose(
            cfg, tmp_path, "test-proj",
            session_id="sess-001", cwd=str(tmp_path),
        )


# ------------------------------------------------------------------ #
# Tier 3: autopilot on → registers eos-sweep job                      #
# ------------------------------------------------------------------ #

def test_autopilot_on_activates_kill_switch(tmp_path: Path, monkeypatch, capsys):
    from mnemo.cli.runtime import main as cli_main
    from mnemo.autopilot.core.kill_switch import is_active

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: tmp_path, raising=False)
    monkeypatch.setattr(
        "mnemo.autopilot.core.labels.ensure_label_exists",
        lambda: None, raising=False,
    )
    cli_main(["autopilot", "on"])
    # Hook-driven scheduling: presence of an active kill switch is what
    # gates SessionStart/SessionEnd autopilot triggers — no fake cron file.
    assert is_active(vault_root=tmp_path) is True
