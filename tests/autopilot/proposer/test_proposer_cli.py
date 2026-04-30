"""Tests for new autopilot CLI subcommands: propose, preempt, proposals."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mnemo.cli.runtime import main


def _run(monkeypatch, tmp_path: Path, *args: str, capsys) -> tuple[int, str]:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: tmp_path, raising=False)
    rc = main([*args])
    out, _err = capsys.readouterr()
    return rc, out


# --- propose ---

def test_propose_no_signals_prints_zero(monkeypatch, tmp_path, capsys):
    with patch(
        "mnemo.autopilot.proposer.eos_extractor.git_log_since",
        return_value=[],
    ), patch(
        "mnemo.cli.commands.autopilot._cwd",
        return_value=tmp_path,
    ), patch(
        "mnemo.core.agent.resolve_canonical_agent",
        return_value=type("A", (), {"name": "test-proj"})(),
    ):
        rc, out = _run(
            monkeypatch, tmp_path,
            "autopilot", "propose", "--session-id", "sess-001",
            capsys=capsys,
        )
    assert rc == 0
    assert "0 candidate" in out


def test_propose_with_signals_lists_candidates(monkeypatch, tmp_path, capsys):
    messages = [
        "normalize price value",
        "normalize price format",
        "normalize price input",
    ]
    with patch(
        "mnemo.autopilot.proposer.eos_extractor.git_log_since",
        return_value=messages,
    ), patch(
        "mnemo.cli.commands.autopilot._cwd",
        return_value=tmp_path,
    ), patch(
        "mnemo.core.agent.resolve_canonical_agent",
        return_value=type("A", (), {"name": "test-proj"})(),
    ):
        rc, out = _run(
            monkeypatch, tmp_path,
            "autopilot", "propose", "--session-id", "sess-patterns",
            capsys=capsys,
        )
    assert rc == 0
    assert "1 candidate" in out
    assert "normalize-price" in out


# --- preempt ---

def test_preempt_writes_cache_and_prints(monkeypatch, tmp_path, capsys):
    with patch(
        "mnemo.autopilot.proposer._hooks.predict_next_action",
        return_value=["slug-a", "slug-b"],
    ), patch(
        "mnemo.autopilot.proposer.preempt.git_current_branch",
        return_value="main",
    ), patch(
        "mnemo.cli.commands.autopilot._cwd",
        return_value=tmp_path,
    ), patch(
        "mnemo.core.agent.resolve_canonical_agent",
        return_value=type("A", (), {"name": "test-proj"})(),
    ):
        rc, out = _run(monkeypatch, tmp_path, "autopilot", "preempt", capsys=capsys)
    assert rc == 0
    assert "2 predicted" in out


# --- proposals list ---

def test_proposals_list_empty(monkeypatch, tmp_path, capsys):
    rc, out = _run(monkeypatch, tmp_path, "autopilot", "proposals", "list", capsys=capsys)
    assert rc == 0
    assert "none" in out.lower()


def test_proposals_list_shows_proposals(monkeypatch, tmp_path, capsys):
    from mnemo.autopilot.core.proposals import write_proposal

    write_proposal(
        vault_root=tmp_path,
        kind="rule_candidate",
        source="tier3.eos_extractor",
        payload={"slug_hint": "normalize-price"},
        project="myproj",
        confidence=0.5,
    )
    write_proposal(
        vault_root=tmp_path,
        kind="dead_rule",
        source="tier0.miss",
        payload={},
        project="myproj",
        confidence=0.3,
    )
    rc, out = _run(monkeypatch, tmp_path, "autopilot", "proposals", "list", capsys=capsys)
    assert rc == 0
    assert "rule_candidate" in out
    assert "dead_rule" in out
    assert "2 proposal" in out


def test_proposals_list_filter_by_kind(monkeypatch, tmp_path, capsys):
    from mnemo.autopilot.core.proposals import write_proposal

    write_proposal(
        vault_root=tmp_path, kind="rule_candidate", source="x", payload={}, project="p"
    )
    write_proposal(
        vault_root=tmp_path, kind="dead_rule", source="y", payload={}, project="p"
    )
    rc, out = _run(
        monkeypatch, tmp_path,
        "autopilot", "proposals", "list", "--kind", "rule_candidate",
        capsys=capsys,
    )
    assert rc == 0
    assert "rule_candidate" in out
    assert "dead_rule" not in out


# --- proposals review ---

def test_proposals_review_accept_flag(monkeypatch, tmp_path, capsys):
    from mnemo.autopilot.core.proposals import write_proposal, list_proposals

    p = write_proposal(
        vault_root=tmp_path, kind="rule_candidate", source="x",
        payload={"slug_hint": "foo"}, project="p",
    )
    rc, out = _run(
        monkeypatch, tmp_path,
        "autopilot", "proposals", "review", "--id", p.id, "--accept",
        capsys=capsys,
    )
    assert rc == 0
    assert "accepted" in out.lower()
    updated = list_proposals(vault_root=tmp_path)[0]
    assert updated.status == "accepted"


def test_proposals_review_reject_flag(monkeypatch, tmp_path, capsys):
    from mnemo.autopilot.core.proposals import write_proposal, list_proposals

    p = write_proposal(
        vault_root=tmp_path, kind="rule_candidate", source="x",
        payload={}, project="p",
    )
    rc, out = _run(
        monkeypatch, tmp_path,
        "autopilot", "proposals", "review", "--id", p.id, "--reject",
        capsys=capsys,
    )
    assert rc == 0
    assert "rejected" in out.lower()
    updated = list_proposals(vault_root=tmp_path)[0]
    assert updated.status == "rejected"


def test_proposals_review_no_pending(monkeypatch, tmp_path, capsys):
    rc, out = _run(
        monkeypatch, tmp_path,
        "autopilot", "proposals", "review",
        capsys=capsys,
    )
    assert rc == 0
    assert "no pending" in out.lower()


def test_proposals_review_missing_id(monkeypatch, tmp_path, capsys):
    rc, out = _run(
        monkeypatch, tmp_path,
        "autopilot", "proposals", "review", "--id", "does-not-exist", "--accept",
        capsys=capsys,
    )
    assert rc == 1
    assert "not found" in out.lower()


# --- parser wiring ---

def test_parser_propose_requires_session_id():
    import sys
    from mnemo.cli.parser import _build_parser

    p = _build_parser()
    # Missing --session-id should cause a parse error
    with pytest.raises(SystemExit):
        p.parse_args(["autopilot", "propose"])


def test_parser_propose_parses_session_id():
    from mnemo.cli.parser import _build_parser

    p = _build_parser()
    ns = p.parse_args(["autopilot", "propose", "--session-id", "abc123"])
    assert ns.autopilot_action == "propose"
    assert ns.session_id == "abc123"


def test_parser_preempt_parses():
    from mnemo.cli.parser import _build_parser

    p = _build_parser()
    ns = p.parse_args(["autopilot", "preempt"])
    assert ns.autopilot_action == "preempt"


def test_parser_proposals_list_parses():
    from mnemo.cli.parser import _build_parser

    p = _build_parser()
    ns = p.parse_args(["autopilot", "proposals", "list"])
    assert ns.proposals_action == "list"


def test_parser_proposals_list_with_filters():
    from mnemo.cli.parser import _build_parser

    p = _build_parser()
    ns = p.parse_args([
        "autopilot", "proposals", "list",
        "--status", "pending", "--kind", "rule_candidate", "--project", "myproj",
    ])
    assert ns.status == "pending"
    assert ns.kind == "rule_candidate"
    assert ns.project == "myproj"


def test_parser_proposals_review_parses():
    from mnemo.cli.parser import _build_parser

    p = _build_parser()
    ns = p.parse_args(["autopilot", "proposals", "review", "--id", "abc", "--accept"])
    assert ns.proposals_action == "review"
    assert ns.id == "abc"
    assert ns.accept is True
