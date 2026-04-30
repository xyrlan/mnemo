"""Tests for doctor_fixer — detect + fix doctor warnings."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mnemo.autopilot.selffix.doctor_fixer import (
    DoctorWarning,
    detect_fixable,
    fix_warning,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_rule(tmp_path: Path, name: str, sources: list, body: str = "x" * 60) -> Path:
    """Write a minimal rule md file with given sources."""
    shared_dir = tmp_path / "shared" / "feedback"
    shared_dir.mkdir(parents=True, exist_ok=True)
    content = f"""---
type: feedback
tags:
  - test
sources:
{chr(10).join(f'  - {s}' for s in sources)}
---
{body}
"""
    path = shared_dir / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# DoctorWarning dataclass
# ---------------------------------------------------------------------------


def test_doctor_warning_has_required_fields() -> None:
    w = DoctorWarning(
        kind="source_path_missing",
        rule_path=Path("/some/rule.md"),
        detail="briefings/missing.md",
    )
    assert w.kind == "source_path_missing"
    assert w.rule_path == Path("/some/rule.md")
    assert w.detail == "briefings/missing.md"


def test_doctor_warning_auto_fixable_true() -> None:
    w = DoctorWarning(kind="source_path_missing", rule_path=Path("/r.md"), detail="x")
    assert w.auto_fixable is True


def test_doctor_warning_auto_fixable_false_for_unsupported() -> None:
    w = DoctorWarning(kind="body_too_short", rule_path=Path("/r.md"), detail="x")
    assert w.auto_fixable is False


# ---------------------------------------------------------------------------
# detect_fixable — source_path_missing
# ---------------------------------------------------------------------------


def test_detect_fixable_finds_missing_source(tmp_path: Path) -> None:
    """A rule referencing a non-existent source should surface as fixable."""
    _make_rule(
        tmp_path,
        "my-rule",
        sources=["briefings/nonexistent.md"],
    )
    warnings = detect_fixable(vault_root=tmp_path)
    assert len(warnings) == 1
    assert warnings[0].kind == "source_path_missing"
    assert "briefings/nonexistent.md" in warnings[0].detail


def test_detect_fixable_ignores_present_sources(tmp_path: Path) -> None:
    """A rule with a source that resolves must NOT appear in warnings."""
    src = tmp_path / "briefings" / "session.md"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("# session", encoding="utf-8")
    _make_rule(tmp_path, "my-rule", sources=["briefings/session.md"])
    warnings = detect_fixable(vault_root=tmp_path)
    assert warnings == []


def test_detect_fixable_returns_empty_when_no_shared_dir(tmp_path: Path) -> None:
    warnings = detect_fixable(vault_root=tmp_path)
    assert warnings == []


def test_detect_fixable_filters_non_fixable_kinds(tmp_path: Path) -> None:
    """Only auto-fixable kinds must be returned."""
    _make_rule(tmp_path, "my-rule", sources=["briefings/nonexistent.md"])
    warnings = detect_fixable(vault_root=tmp_path)
    for w in warnings:
        assert w.auto_fixable is True


# ---------------------------------------------------------------------------
# fix_warning — source_path_missing
# ---------------------------------------------------------------------------


def test_fix_warning_strips_missing_source_line(tmp_path: Path) -> None:
    rule_path = _make_rule(
        tmp_path, "my-rule", sources=["briefings/missing.md", "briefings/also-missing.md"]
    )
    warning = DoctorWarning(
        kind="source_path_missing",
        rule_path=rule_path,
        detail="briefings/missing.md",
    )
    modified = fix_warning(warning, vault_root=tmp_path)
    assert modified == rule_path
    text = rule_path.read_text(encoding="utf-8")
    assert "briefings/missing.md" not in text
    # The other source should still be there
    assert "briefings/also-missing.md" in text


def test_fix_warning_strips_only_target_source(tmp_path: Path) -> None:
    """fix_warning must not remove sources it wasn't asked to remove."""
    src = tmp_path / "briefings" / "present.md"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("# s", encoding="utf-8")
    rule_path = _make_rule(
        tmp_path, "my-rule", sources=["briefings/present.md", "briefings/gone.md"]
    )
    warning = DoctorWarning(
        kind="source_path_missing",
        rule_path=rule_path,
        detail="briefings/gone.md",
    )
    fix_warning(warning, vault_root=tmp_path)
    text = rule_path.read_text(encoding="utf-8")
    assert "briefings/present.md" in text
    assert "briefings/gone.md" not in text


def test_fix_warning_raises_for_unknown_kind(tmp_path: Path) -> None:
    rule_path = tmp_path / "shared" / "feedback" / "r.md"
    rule_path.parent.mkdir(parents=True, exist_ok=True)
    rule_path.write_text("---\ntype: feedback\n---\nbody\n", encoding="utf-8")
    warning = DoctorWarning(kind="unknown_kind", rule_path=rule_path, detail="x")
    with pytest.raises(ValueError, match="unknown_kind"):
        fix_warning(warning, vault_root=tmp_path)


# ---------------------------------------------------------------------------
# open_doctor_fix_pr
# ---------------------------------------------------------------------------


def test_open_doctor_fix_pr_skips_when_budget_exhausted(tmp_path: Path) -> None:
    from mnemo.autopilot.selffix.doctor_fixer import open_doctor_fix_pr

    with patch(
        "mnemo.autopilot.selffix.doctor_fixer.pr_budget.can_open",
        return_value=(False, "daily cap reached"),
    ):
        result = open_doctor_fix_pr(
            warnings=[
                DoctorWarning(
                    kind="source_path_missing",
                    rule_path=tmp_path / "shared" / "feedback" / "r.md",
                    detail="briefings/gone.md",
                )
            ],
            vault_root=tmp_path,
            repo_root=tmp_path,
        )
    assert result is None


def test_open_doctor_fix_pr_dry_run_no_pr_opened(tmp_path: Path) -> None:
    from mnemo.autopilot.selffix.doctor_fixer import open_doctor_fix_pr

    rule_path = _make_rule(tmp_path, "my-rule", sources=["briefings/missing.md"])
    warnings = [
        DoctorWarning(
            kind="source_path_missing",
            rule_path=rule_path,
            detail="briefings/missing.md",
        )
    ]
    # Enable kill switch
    (tmp_path / ".mnemo").mkdir(exist_ok=True)
    import json
    (tmp_path / ".mnemo" / "autopilot.json").write_text(
        json.dumps({"schema_version": 1, "state": "on", "paused_until": None,
                    "last_changed_at": None, "last_changed_by": None})
    )

    with patch("mnemo.autopilot.selffix.doctor_fixer._gh.open_pr") as mock_pr:
        result = open_doctor_fix_pr(
            warnings=warnings,
            vault_root=tmp_path,
            repo_root=tmp_path,
            dry_run=True,
        )
    mock_pr.assert_not_called()
    assert result is None


def test_open_doctor_fix_pr_records_budget_on_success(tmp_path: Path) -> None:
    from mnemo.autopilot.selffix.doctor_fixer import open_doctor_fix_pr

    rule_path = _make_rule(tmp_path, "my-rule", sources=["briefings/missing.md"])
    warnings = [
        DoctorWarning(
            kind="source_path_missing",
            rule_path=rule_path,
            detail="briefings/missing.md",
        )
    ]
    (tmp_path / ".mnemo").mkdir(exist_ok=True)
    import json
    (tmp_path / ".mnemo" / "autopilot.json").write_text(
        json.dumps({"schema_version": 1, "state": "on", "paused_until": None,
                    "last_changed_at": None, "last_changed_by": None})
    )

    with patch("mnemo.autopilot.selffix.doctor_fixer._gh.create_branch", return_value="branch") as _cb, \
         patch("mnemo.autopilot.selffix.doctor_fixer._gh.push_branch", return_value=True) as _pb, \
         patch("mnemo.autopilot.selffix.doctor_fixer._gh.open_pr", return_value=99) as _op, \
         patch("mnemo.autopilot.selffix.doctor_fixer.pr_budget.record_opened") as mock_rec, \
         patch("mnemo.autopilot.selffix.doctor_fixer._run_pytest", return_value=True):
        result = open_doctor_fix_pr(
            warnings=warnings,
            vault_root=tmp_path,
            repo_root=tmp_path,
        )
    assert result == 99
    mock_rec.assert_called_once()


def test_open_doctor_fix_pr_aborts_when_pytest_fails(tmp_path: Path) -> None:
    from mnemo.autopilot.selffix.doctor_fixer import open_doctor_fix_pr

    rule_path = _make_rule(tmp_path, "my-rule", sources=["briefings/missing.md"])
    warnings = [
        DoctorWarning(
            kind="source_path_missing",
            rule_path=rule_path,
            detail="briefings/missing.md",
        )
    ]
    (tmp_path / ".mnemo").mkdir(exist_ok=True)
    import json
    (tmp_path / ".mnemo" / "autopilot.json").write_text(
        json.dumps({"schema_version": 1, "state": "on", "paused_until": None,
                    "last_changed_at": None, "last_changed_by": None})
    )

    with patch("mnemo.autopilot.selffix.doctor_fixer._gh.create_branch", return_value="branch"), \
         patch("mnemo.autopilot.selffix.doctor_fixer._run_pytest", return_value=False), \
         patch("mnemo.autopilot.selffix.doctor_fixer._gh.open_pr") as mock_pr:
        result = open_doctor_fix_pr(
            warnings=warnings,
            vault_root=tmp_path,
            repo_root=tmp_path,
        )
    mock_pr.assert_not_called()
    assert result is None
