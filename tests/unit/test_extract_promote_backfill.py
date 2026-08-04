"""Origin gate for the project-type 1:1 promotion path (Task 6c).

``promote_projects`` writes straight to ``shared/project/`` with no _inbox
and, until Task 6c, no origin check — so a harvested page stamped
``metadata.origin: backfill`` landed unreviewed in the sacred dir. Backfill
project pages must stage in ``shared/_inbox/project/`` instead.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.extract import promote, scanner


def _mk_project_file(
    tmp_vault: Path,
    agent: str,
    stem: str,
    body: str = "project body",
    *,
    backfill: bool = False,
) -> scanner.MemoryFile:
    """Write a project-type memory file and read it back through the scanner.

    ``backfill`` writes the harvest stamp the way harvest writes it: nested
    under ``metadata:``. ``parse_frontmatter`` is a flat line reader, so it
    lands at the top level of ``.frontmatter`` — which is exactly what the
    gate has to read.
    """
    mem_dir = tmp_vault / "bots" / agent / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    path = mem_dir / f"{stem}.md"
    stamp = "metadata:\n  origin: backfill\n" if backfill else ""
    content = f"---\nname: {stem}\ndescription: desc\ntype: project\n{stamp}---\n{body}\n"
    path.write_text(content)
    return scanner._read_memory_file(path, agent=agent)


def test_flat_parser_lifts_nested_origin_to_top_level(tmp_vault: Path):
    """Guard the assumption the gate rests on: the stamp round-trips flat."""
    f = _mk_project_file(tmp_vault, "a", "project_x", backfill=True)
    assert f.frontmatter.get("origin") == "backfill"


def test_real_harvest_output_stages_in_inbox(tmp_vault: Path):
    """Reach the gate through harvest's own renderer, not a hand-built fixture.

    Task 6 shipped a gate that read the wrong frontmatter key precisely because
    every test hand-built its input; this one round-trips the real shape.
    """
    from mnemo.core.backfill.harvest import _render_memory_file

    mem_dir = tmp_vault / "bots" / "alpha" / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    path = mem_dir / "project_deploy_pipeline.md"
    path.write_text(
        _render_memory_file(
            slug="project_deploy_pipeline",
            page_type="project",
            name="Deploy pipeline",
            description="How deploys work",
            body="Reconstructed project context.",
            session_id="0c8f-uuid",
        )
    )
    f = scanner._read_memory_file(path, agent="alpha")
    assert f.type == "project"

    state = scanner.ExtractionState(last_run=None, entries={})
    promote.promote_projects([f], state, tmp_vault)

    slug = f"alpha__{f.slug}"
    assert (tmp_vault / "shared" / "_inbox" / "project" / f"{slug}.md").exists()
    assert not (tmp_vault / "shared" / "project" / f"{slug}.md").exists()


def test_backfill_project_file_stages_in_inbox(tmp_vault: Path):
    state = scanner.ExtractionState(last_run=None, entries={})
    f = _mk_project_file(tmp_vault, "sg-imports", "project_china_portal", backfill=True)
    promote.promote_projects([f], state, tmp_vault)

    staged = tmp_vault / "shared" / "_inbox" / "project" / "sg-imports__china-portal.md"
    promoted = tmp_vault / "shared" / "project" / "sg-imports__china-portal.md"
    assert staged.exists(), "backfill project page must stage in shared/_inbox/project/"
    assert not promoted.exists(), "backfill project page must not reach shared/project/"


def test_unstamped_project_file_still_promotes_directly(tmp_vault: Path):
    """Control: live project files keep the pre-Task-6c behaviour exactly."""
    state = scanner.ExtractionState(last_run=None, entries={})
    f = _mk_project_file(tmp_vault, "sg-imports", "project_china_portal")
    result = promote.promote_projects([f], state, tmp_vault)

    promoted = tmp_vault / "shared" / "project" / "sg-imports__china-portal.md"
    staged = tmp_vault / "shared" / "_inbox" / "project" / "sg-imports__china-portal.md"
    assert promoted.exists()
    assert not staged.exists()
    assert result.written_fresh == ["project/sg-imports__china-portal"]


def test_staged_project_page_keeps_the_origin_stamp(tmp_vault: Path):
    """The staged file stays self-describing, in the spelling both readers share.

    Asserted at the **text** level, and through the *consumer's* parser. The
    flat ``scanner.parse_frontmatter`` lifts nested keys to the top level, so a
    ``scanner``-only assertion here cannot tell ``origin: backfill`` from
    ``metadata:\\n  origin: backfill`` — and the doctor advisory reads this file
    with the nesting-aware ``filters.parse_frontmatter``. A mutation to the
    nested spelling survived the entire suite until this assertion changed.
    """
    from mnemo.core.filters import parse_frontmatter as filters_parse

    state = scanner.ExtractionState(last_run=None, entries={})
    f = _mk_project_file(tmp_vault, "a", "project_x", backfill=True)
    promote.promote_projects([f], state, tmp_vault)

    staged = tmp_vault / "shared" / "_inbox" / "project" / "a__x.md"
    text = staged.read_text()
    assert "\norigin: backfill\n" in text, "stamp must be written top-level"
    assert filters_parse(text).get("origin") == "backfill"
    fm, _body = scanner.parse_frontmatter(text)
    assert fm.get("origin") == "backfill"


def test_promoted_project_page_carries_no_origin_stamp(tmp_vault: Path):
    """Control: the stamp is not emitted unconditionally."""
    state = scanner.ExtractionState(last_run=None, entries={})
    f = _mk_project_file(tmp_vault, "a", "project_x")
    promote.promote_projects([f], state, tmp_vault)

    promoted = tmp_vault / "shared" / "project" / "a__x.md"
    fm, _body = scanner.parse_frontmatter(promoted.read_text())
    assert "origin" not in fm


def test_staged_project_entry_is_recorded_as_inbox(tmp_vault: Path):
    """State must not claim a staged page was promoted.

    ``status="direct"`` means "written to shared/<type>/"; readers that check
    for the promoted file (the universal reconciler, doctor) would look in the
    wrong place. Staged pages use the same ``inbox`` status every other
    _inbox-staged page uses; the file's ``origin`` key is what distinguishes
    them.
    """
    state = scanner.ExtractionState(last_run=None, entries={})
    f = _mk_project_file(tmp_vault, "a", "project_x", backfill=True)
    promote.promote_projects([f], state, tmp_vault)
    assert state.entries["project/a__x"].status == "inbox"


def test_live_project_entry_still_recorded_as_direct(tmp_vault: Path):
    state = scanner.ExtractionState(last_run=None, entries={})
    f = _mk_project_file(tmp_vault, "a", "project_x")
    promote.promote_projects([f], state, tmp_vault)
    assert state.entries["project/a__x"].status == "direct"


def test_staged_project_page_is_idempotent_across_runs(tmp_vault: Path):
    """A second extract must not re-route the staged page into shared/project/."""
    state = scanner.ExtractionState(last_run=None, entries={})
    f = _mk_project_file(tmp_vault, "a", "project_x", backfill=True)
    promote.promote_projects([f], state, tmp_vault)
    result = promote.promote_projects([f], state, tmp_vault)

    assert "project/a__x" in result.unchanged_skipped
    assert not (tmp_vault / "shared" / "project" / "a__x.md").exists()


def test_edited_backfill_source_rewrites_the_staged_page(tmp_vault: Path):
    """Source churn keeps updating the staged copy, never the sacred dir."""
    state = scanner.ExtractionState(last_run=None, entries={})
    f = _mk_project_file(tmp_vault, "a", "project_x", body="v1", backfill=True)
    promote.promote_projects([f], state, tmp_vault)
    f2 = _mk_project_file(tmp_vault, "a", "project_x", body="v2", backfill=True)
    result = promote.promote_projects([f2], state, tmp_vault)

    staged = tmp_vault / "shared" / "_inbox" / "project" / "a__x.md"
    assert "v2" in staged.read_text()
    assert "project/a__x" in result.overwrite_safe
    assert not (tmp_vault / "shared" / "project" / "a__x.md").exists()
