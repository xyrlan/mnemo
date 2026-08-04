"""Doctor's unpromoted-universal advisory vs. backfill-staged _inbox pages (Task 6c).

Backfill pages sit at ``status="inbox"`` permanently and by design, so the
"run 'mnemo extract' to reconcile" warning would fire forever with advice that
never works. They must be reported as a neutral status line instead.

Fixtures feed the check from the **real page renderers** wherever possible.
Hand-built frontmatter is how Task 6's gate came to read the wrong key, and how
this file's first draft failed to notice that its two readers disagreed about
nested vs. top-level stamps.
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


def _rendered_cluster_page(slug: str, page_type: str, *, backfill: bool) -> str:
    """Real ``_render_page`` output — the writer for staged cluster pages."""
    from mnemo.core.extract.inbox.rendering import _render_page
    from mnemo.core.extract.inbox.types import ExtractedPage

    return _render_page(
        ExtractedPage(
            slug=slug,
            type=page_type,
            name=slug,
            description="desc",
            body="Some rule body.",
            source_files=["bots/alpha/memory/a.md", "bots/beta/memory/b.md"],
            source_hash="sha256:x",
            origin_backfill=backfill,
        ),
        run_id="2026-08-01T00:00:00",
    )


def _rendered_project_page(agent: str, stem: str, *, backfill: bool) -> str:
    """Real ``_render_project_page`` output — the writer this task added."""
    from mnemo.core.extract.promote import _render_project_page
    from mnemo.core.extract.scanner import MemoryFile

    fm = {"name": stem, "description": "desc"}
    if backfill:
        fm["origin"] = "backfill"
    return _render_project_page(
        MemoryFile(
            path=Path(f"bots/{agent}/memory/project_{stem}.md"),
            agent=agent,
            type="project",
            slug=stem,
            frontmatter=fm,
            body="Some project context.",
            source_hash="sha256:x",
        ),
        run_id="2026-08-01T00:00:00",
    )


def _place(vault: Path, page_type: str, slug: str, text: str) -> None:
    d = vault / "shared" / "_inbox" / page_type
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.md").write_text(text, encoding="utf-8")


def _line_containing(out: str, needle: str) -> str:
    matches = [ln for ln in out.splitlines() if needle in ln]
    assert matches, f"no line containing {needle!r} in:\n{out}"
    return matches[0]


def test_backfill_staged_entries_produce_no_backlog_warning(
    tmp_vault: Path, capsys: pytest.CaptureFixture
):
    _write_state(
        tmp_vault,
        {"feedback/x": _entry("bots/alpha/memory/a.md", "bots/beta/memory/b.md")},
    )
    _place(tmp_vault, "feedback", "x", _rendered_cluster_page("x", "feedback", backfill=True))

    assert rules._doctor_check_unpromoted_universal_candidates(tmp_vault) is True
    out = capsys.readouterr().out
    assert "cross universalThreshold" not in out
    assert "mnemo extract" not in out

    line = _line_containing(out, "backfill rule(s) staged")
    assert "1 backfill rule(s) staged in _inbox/ awaiting review" in line
    # The defining property: this is a status line, not a warning.
    assert "⚠" not in line


def test_live_backlog_still_warns(tmp_vault: Path, capsys: pytest.CaptureFixture):
    """Control: a genuine cross-project _inbox entry keeps its warning."""
    _write_state(
        tmp_vault,
        {"feedback/x": _entry("bots/alpha/memory/a.md", "bots/beta/memory/b.md")},
    )
    _place(tmp_vault, "feedback", "x", _rendered_cluster_page("x", "feedback", backfill=False))

    assert rules._doctor_check_unpromoted_universal_candidates(tmp_vault) is True
    out = capsys.readouterr().out
    assert "1 rule(s) cross universalThreshold" in out
    assert "mnemo extract" in out
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
    _place(tmp_vault, "feedback", "live",
           _rendered_cluster_page("live", "feedback", backfill=False))
    _place(tmp_vault, "feedback", "staged",
           _rendered_cluster_page("staged", "feedback", backfill=True))
    _place(tmp_vault, "project", "alpha__x",
           _rendered_project_page("alpha", "x", backfill=True))

    assert rules._doctor_check_unpromoted_universal_candidates(tmp_vault) is True
    out = capsys.readouterr().out
    before_status, _, after_status = out.partition("backfill rule(s) staged")

    warning = _line_containing(out, "cross universalThreshold")
    assert "1 rule(s) cross universalThreshold" in warning

    # Only the live entry is listed as a backlog item; the staged one is not.
    assert "feedback/live" in before_status
    assert "feedback/staged" not in before_status

    status = _line_containing(out, "backfill rule(s) staged")
    # Single-project staged project pages never cross the threshold, so a
    # threshold-scoped count would under-report them.
    assert "2 backfill rule(s) staged in _inbox/ awaiting review" in status
    assert "⚠" not in status
    assert "shared/_inbox/project/alpha__x.md" in after_status
    # The advice that does not work must not follow the neutral line.
    assert "mnemo extract" in before_status
    assert "mnemo extract" not in after_status


def test_status_line_names_the_concrete_review_move(
    tmp_vault: Path, capsys: pytest.CaptureFixture
):
    _write_state(tmp_vault, {"project/alpha__x": _entry("bots/alpha/memory/project_x.md")})
    _place(tmp_vault, "project", "alpha__x", _rendered_project_page("alpha", "x", backfill=True))

    rules._doctor_check_unpromoted_universal_candidates(tmp_vault)
    out = capsys.readouterr().out
    assert "shared/_inbox/project/ → shared/project/" in out


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


def test_nested_origin_stamp_is_also_recognised(tmp_vault: Path):
    """The two spellings must not diverge again.

    ``filters.parse_frontmatter`` keeps a nested key nested, so a page carrying
    harvest's ``metadata:`` spelling would read back as origin-less under a
    naive top-level check. The shared predicate accepts both.
    """
    _place(
        tmp_vault, "feedback", "x",
        "---\nname: x\ntype: feedback\nmetadata:\n  origin: backfill\n---\n\nbody\n",
    )
    assert rules._is_backfill_staged(tmp_vault, "feedback/x") is True
