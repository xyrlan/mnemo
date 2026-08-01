"""Doctor's unpromoted-universal advisory vs. backfill-staged _inbox pages (Task 6c).

Backfill pages sit at ``status="inbox"`` permanently and by design, so the
"run 'mnemo extract' to reconcile" warning would fire forever with advice that
never works. They must be reported as a neutral status line instead.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.cli.commands.doctor_checks import rules


def _write_state(vault: Path, entries: dict) -> None:
    (vault / ".mnemo").mkdir(parents=True, exist_ok=True)
    (vault / ".mnemo" / "extraction-state.json").write_text(
        json.dumps({"schema_version": 2, "last_run": None, "entries": entries}),
        encoding="utf-8",
    )


def _entry(*source_files: str) -> dict:
    return {
        "source_files": list(source_files),
        "source_hash": "sha256:x",
        "written_hash": "sha256:y",
        "written_at": "2026-08-01T00:00:00",
        "status": "inbox",
    }


def _write_inbox_page(vault: Path, page_type: str, slug: str, *, backfill: bool) -> None:
    d = vault / "shared" / "_inbox" / page_type
    d.mkdir(parents=True, exist_ok=True)
    origin = "origin: backfill\n" if backfill else ""
    (d / f"{slug}.md").write_text(
        f"---\nname: {slug}\ntype: {page_type}\n{origin}---\n\nbody\n", encoding="utf-8"
    )


def test_backfill_staged_entries_produce_no_backlog_warning(
    tmp_vault: Path, capsys: pytest.CaptureFixture
):
    _write_state(
        tmp_vault,
        {"feedback/x": _entry("bots/alpha/memory/a.md", "bots/beta/memory/b.md")},
    )
    _write_inbox_page(tmp_vault, "feedback", "x", backfill=True)

    assert rules._doctor_check_unpromoted_universal_candidates(tmp_vault) is True
    out = capsys.readouterr().out
    assert "cross universalThreshold" not in out
    assert "mnemo extract" not in out
    assert "1 backfill rule(s) staged in _inbox/ awaiting review" in out


def test_live_backlog_still_warns(tmp_vault: Path, capsys: pytest.CaptureFixture):
    """Control: a genuine cross-project _inbox entry keeps its warning."""
    _write_state(
        tmp_vault,
        {"feedback/x": _entry("bots/alpha/memory/a.md", "bots/beta/memory/b.md")},
    )
    _write_inbox_page(tmp_vault, "feedback", "x", backfill=False)

    assert rules._doctor_check_unpromoted_universal_candidates(tmp_vault) is True
    out = capsys.readouterr().out
    assert "1 rule(s) cross universalThreshold" in out
    assert "backfill rule(s) staged" not in out


def test_mixed_vault_reports_both_populations_separately(
    tmp_vault: Path, capsys: pytest.CaptureFixture
):
    _write_state(
        tmp_vault,
        {
            "feedback/live": _entry("bots/alpha/memory/a.md", "bots/beta/memory/b.md"),
            "feedback/staged": _entry("bots/alpha/memory/c.md", "bots/beta/memory/d.md"),
            "project/alpha__x": _entry("bots/alpha/memory/project_x.md"),
        },
    )
    _write_inbox_page(tmp_vault, "feedback", "live", backfill=False)
    _write_inbox_page(tmp_vault, "feedback", "staged", backfill=True)
    _write_inbox_page(tmp_vault, "project", "alpha__x", backfill=True)

    rules._doctor_check_unpromoted_universal_candidates(tmp_vault)
    out = capsys.readouterr().out
    assert "1 rule(s) cross universalThreshold" in out
    assert "feedback/live" in out
    assert "feedback/staged" not in out
    # Single-project staged project pages never cross the threshold, so a
    # threshold-scoped count would under-report them.
    assert "2 backfill rule(s) staged in _inbox/ awaiting review" in out


def test_no_state_file_is_silent(tmp_vault: Path, capsys: pytest.CaptureFixture):
    assert rules._doctor_check_unpromoted_universal_candidates(tmp_vault) is True
    assert capsys.readouterr().out == ""


def test_missing_inbox_file_is_treated_as_live(
    tmp_vault: Path, capsys: pytest.CaptureFixture
):
    """A deleted _inbox file can't prove backfill origin — keep the old warning."""
    _write_state(
        tmp_vault,
        {"feedback/x": _entry("bots/alpha/memory/a.md", "bots/beta/memory/b.md")},
    )

    rules._doctor_check_unpromoted_universal_candidates(tmp_vault)
    out = capsys.readouterr().out
    assert "1 rule(s) cross universalThreshold" in out
