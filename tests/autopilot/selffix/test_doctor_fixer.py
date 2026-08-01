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
    """A rule referencing a non-existent source should surface as fixable.

    A second, resolvable source keeps the rule's provenance alive — stripping
    the only source is refused (see the provenance-guard tests below).
    """
    keep = tmp_path / "briefings" / "keep.md"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text("x", encoding="utf-8")
    _make_rule(
        tmp_path,
        "my-rule",
        sources=["briefings/nonexistent.md", "briefings/keep.md"],
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

    keep = tmp_path / "briefings" / "keep.md"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text("x", encoding="utf-8")
    rule_path = _make_rule(
        tmp_path, "my-rule",
        sources=["briefings/missing.md", "briefings/keep.md"],
    )
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

    keep = tmp_path / "briefings" / "keep.md"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text("x", encoding="utf-8")
    rule_path = _make_rule(
        tmp_path, "my-rule",
        sources=["briefings/missing.md", "briefings/keep.md"],
    )
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

    with patch("mnemo.autopilot.selffix.doctor_fixer._gh.create_worktree",
               return_value=tmp_path / "wt") as _cb, \
         patch("mnemo.autopilot.selffix.doctor_fixer._gh.mirror_paths"), \
         patch("mnemo.autopilot.selffix.doctor_fixer._gh.commit_all", return_value=True), \
         patch("mnemo.autopilot.selffix.doctor_fixer._gh.remove_worktree"), \
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

    keep = tmp_path / "briefings" / "keep.md"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text("x", encoding="utf-8")
    rule_path = _make_rule(
        tmp_path, "my-rule",
        sources=["briefings/missing.md", "briefings/keep.md"],
    )
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

    with patch("mnemo.autopilot.selffix.doctor_fixer._gh.create_worktree",
               return_value=tmp_path / "wt"), \
         patch("mnemo.autopilot.selffix.doctor_fixer._gh.mirror_paths"), \
         patch("mnemo.autopilot.selffix.doctor_fixer._gh.commit_all", return_value=True), \
         patch("mnemo.autopilot.selffix.doctor_fixer._gh.remove_worktree"), \
         patch("mnemo.autopilot.selffix.doctor_fixer._run_pytest", return_value=False), \
         patch("mnemo.autopilot.selffix.doctor_fixer._gh.open_pr") as mock_pr:
        result = open_doctor_fix_pr(
            warnings=warnings,
            vault_root=tmp_path,
            repo_root=tmp_path,
        )
    mock_pr.assert_not_called()
    assert result is None


# ---------------------------------------------------------------------------
# _run_pytest interpreter
# ---------------------------------------------------------------------------


def test_run_pytest_uses_sys_executable(tmp_path: Path) -> None:
    """Gate must spawn pytest via sys.executable, not bare "python".

    On macOS there is often no `python` on PATH (only `python3`), so a bare
    "python" argv raises FileNotFoundError and the gate reports a false
    "pytest failed" — blocking every doctor-fix PR.
    """
    import sys
    from mnemo.autopilot.selffix import doctor_fixer

    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return MagicMock(returncode=0)

    with patch("mnemo.autopilot.selffix.doctor_fixer.subprocess.run", side_effect=fake_run):
        assert doctor_fixer._run_pytest(repo_root=tmp_path) is True
    assert captured["argv"][0] == sys.executable


# ---------------------------------------------------------------------------
# Provenance guard (v0.15.1 dogfood: self-fix was emptying `sources:`)
# ---------------------------------------------------------------------------


def test_last_source_is_not_stripped(tmp_path: Path) -> None:
    """Stripping a rule's only source empties `sources:` and orphans the rule.

    The rule then resolves to zero projects, drops out of per-project scoping,
    and trades a 'source path missing' warning for a worse one.
    """
    rule = _make_rule(tmp_path, "solo", ["briefings/sessions/gone.md"])
    warnings = detect_fixable(vault_root=tmp_path)
    assert warnings == []
    assert "briefings/sessions/gone.md" in rule.read_text(encoding="utf-8")


def test_strips_orphan_source_when_another_survives(tmp_path: Path) -> None:
    """With a live source left over, stripping is safe and still happens."""
    live = tmp_path / "briefings" / "sessions" / "live.md"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text("x", encoding="utf-8")

    rule = _make_rule(
        tmp_path, "pair",
        ["briefings/sessions/gone.md", "briefings/sessions/live.md"],
    )
    warnings = detect_fixable(vault_root=tmp_path)
    assert len(warnings) == 1
    fix_warning(warnings[0], vault_root=tmp_path)

    text = rule.read_text(encoding="utf-8")
    assert "gone.md" not in text
    assert "live.md" in text


def test_fix_source_path_missing_refuses_to_empty_sources(tmp_path: Path) -> None:
    """Direct fix_warning call is guarded too, not just the detector."""
    rule = _make_rule(tmp_path, "solo2", ["briefings/sessions/gone.md"])
    warning = DoctorWarning(
        kind="source_path_missing",
        rule_path=rule,
        detail="briefings/sessions/gone.md",
    )
    with pytest.raises(ValueError, match="only source"):
        fix_warning(warning, vault_root=tmp_path)
    assert "gone.md" in rule.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# sources_empty repair (heals rules the old strip-fixer already orphaned)
# ---------------------------------------------------------------------------


def _write_state(tmp_path: Path, entries: dict) -> None:
    import json

    state_dir = tmp_path / ".mnemo"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "extraction-state.json").write_text(
        json.dumps({"entries": entries}), encoding="utf-8",
    )


def test_detects_empty_sources_recoverable_from_state(tmp_path: Path) -> None:
    rule = _make_rule(tmp_path, "orphaned", [])
    src = tmp_path / "briefings" / "sessions" / "s1.md"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("x", encoding="utf-8")
    _write_state(tmp_path, {
        "feedback/orphaned": {"source_files": ["briefings/sessions/s1.md"]},
    })

    warnings = detect_fixable(vault_root=tmp_path)
    kinds = {w.kind for w in warnings}
    assert "sources_empty" in kinds

    warning = next(w for w in warnings if w.kind == "sources_empty")
    fix_warning(warning, vault_root=tmp_path)

    text = rule.read_text(encoding="utf-8")
    assert "  - briefings/sessions/s1.md" in text


def test_empty_sources_not_fixable_without_state_entry(tmp_path: Path) -> None:
    _make_rule(tmp_path, "unrecoverable", [])
    _write_state(tmp_path, {})
    assert detect_fixable(vault_root=tmp_path) == []


def test_empty_sources_not_fixable_when_state_source_is_gone(tmp_path: Path) -> None:
    """Restoring a path that no longer resolves just recreates the old warning."""
    _make_rule(tmp_path, "stale", [])
    _write_state(tmp_path, {
        "feedback/stale": {"source_files": ["briefings/sessions/deleted.md"]},
    })
    assert detect_fixable(vault_root=tmp_path) == []


# ---------------------------------------------------------------------------
# Source relocation (dead path but the briefing still exists elsewhere)
# ---------------------------------------------------------------------------


def _briefing(tmp_path: Path, project: str, sid: str) -> Path:
    p = tmp_path / "bots" / project / "briefings" / "sessions" / f"{sid}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("briefing", encoding="utf-8")
    return p


def test_relocates_source_instead_of_stripping(tmp_path: Path) -> None:
    """Legacy vault-relative path: the briefing moved under bots/<project>/."""
    sid = "b8f895ca-9f90-4070-88ce-2e3888afb0d3"
    _briefing(tmp_path, "meunu", sid)
    rule = _make_rule(tmp_path, "moved", [f"briefings/sessions/{sid}.md"])

    warnings = detect_fixable(vault_root=tmp_path)
    assert [w.kind for w in warnings] == ["source_path_moved"]
    fix_warning(warnings[0], vault_root=tmp_path)

    text = rule.read_text(encoding="utf-8")
    assert f"  - bots/meunu/briefings/sessions/{sid}.md" in text
    assert f"  - briefings/sessions/{sid}.md" not in text


def test_relocation_corrects_wrong_project_attribution(tmp_path: Path) -> None:
    """A source under the wrong project orphans the rule from real scoping."""
    sid = "aa41cbf4-257c-4168-be43-dee4d718fad6"
    _briefing(tmp_path, "clearframe", sid)
    rule = _make_rule(
        tmp_path, "misattributed",
        [f"bots/clubinho/briefings/sessions/{sid}.md"],
    )

    warnings = detect_fixable(vault_root=tmp_path)
    assert [w.kind for w in warnings] == ["source_path_moved"]
    fix_warning(warnings[0], vault_root=tmp_path)

    assert f"bots/clearframe/briefings/sessions/{sid}.md" in rule.read_text(encoding="utf-8")


def test_ambiguous_relocation_is_not_auto_fixed(tmp_path: Path) -> None:
    """Two candidates with the same basename — a human decides."""
    sid = "dup-session"
    _briefing(tmp_path, "a", sid)
    _briefing(tmp_path, "b", sid)
    _make_rule(tmp_path, "ambiguous", [f"briefings/sessions/{sid}.md"])
    assert detect_fixable(vault_root=tmp_path) == []


def test_empty_sources_healed_via_relocated_state_path(tmp_path: Path) -> None:
    """State's recorded path is stale, but the briefing is still findable."""
    sid = "4282f958-8ffd-4385-9733-330028ae68f2"
    _briefing(tmp_path, "sg-imports", sid)
    rule = _make_rule(tmp_path, "orphan-relocatable", [])
    _write_state(tmp_path, {
        "feedback/orphan-relocatable": {
            "source_files": [f"briefings/sessions/{sid}.md"],
        },
    })

    warnings = detect_fixable(vault_root=tmp_path)
    assert [w.kind for w in warnings] == ["sources_empty"]
    fix_warning(warnings[0], vault_root=tmp_path)

    assert f"  - bots/sg-imports/briefings/sessions/{sid}.md" in rule.read_text(encoding="utf-8")


def test_relocates_extensionless_source_path(tmp_path: Path) -> None:
    """Some state entries recorded the session id without the .md suffix."""
    sid = "f1186308-b629-488d-896c-adeaf74f4b59"
    _briefing(tmp_path, "sg-imports", sid)
    rule = _make_rule(tmp_path, "noext", [f"briefings/sessions/{sid}"])

    warnings = detect_fixable(vault_root=tmp_path)
    assert [w.kind for w in warnings] == ["source_path_moved"]
    fix_warning(warnings[0], vault_root=tmp_path)

    assert f"bots/sg-imports/briefings/sessions/{sid}.md" in rule.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Absolute source-path normalization (v0.15.1 dogfood: 46% of rules absolute)
# ---------------------------------------------------------------------------


def test_detects_and_relativizes_absolute_source(tmp_path: Path) -> None:
    src_rel = "bots/meunu/briefings/sessions/s.md"
    src_abs = str(tmp_path / src_rel)
    briefing = tmp_path / src_rel
    briefing.parent.mkdir(parents=True, exist_ok=True)
    briefing.write_text("x", encoding="utf-8")
    rule = _make_rule(tmp_path, "abs-src", [src_abs])

    warnings = detect_fixable(vault_root=tmp_path)
    assert [w.kind for w in warnings] == ["source_path_absolute"]
    fix_warning(warnings[0], vault_root=tmp_path)

    text = rule.read_text(encoding="utf-8")
    assert f"  - {src_rel}" in text
    assert src_abs not in text


def test_absolute_source_outside_vault_is_not_auto_fixed(tmp_path: Path) -> None:
    _make_rule(tmp_path, "foreign", ["/etc/passwd"])
    kinds = {w.kind for w in detect_fixable(vault_root=tmp_path)}
    assert "source_path_absolute" not in kinds


def _make_project_rule(tmp_path: Path, name: str, sources: list) -> Path:
    d = tmp_path / "shared" / "project"
    d.mkdir(parents=True, exist_ok=True)
    content = (
        "---\ntype: project\ntags:\n  - test\nsources:\n"
        + "\n".join(f"  - {s}" for s in sources)
        + "\n---\n" + "x" * 60 + "\n"
    )
    path = d / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_project_subtype_absolute_source_is_fixed(tmp_path: Path) -> None:
    """shared/project/ rules carried the most absolute paths — scan them too."""
    src_rel = "bots/bingx-robot/memory/foo.md"
    briefing = tmp_path / src_rel
    briefing.parent.mkdir(parents=True, exist_ok=True)
    briefing.write_text("x", encoding="utf-8")
    rule = _make_project_rule(tmp_path, "bingx__foo", [str(tmp_path / src_rel)])

    warnings = detect_fixable(vault_root=tmp_path)
    assert [w.kind for w in warnings] == ["source_path_absolute"]
    fix_warning(warnings[0], vault_root=tmp_path)
    assert f"  - {src_rel}" in rule.read_text(encoding="utf-8")
