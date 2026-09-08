"""Relocate ``.proposed.md`` siblings out of the live rule directories (#155).

The extractor stages a rewrite of a page a human may have edited as a sibling
file for review. One write site wrote that sibling *beside* the target rather
than through ``_sibling_path``, leaving drafts in ``shared/<type>/`` where they
shadow the rule they propose to replace.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.migrations import proposed


def _page(vault: Path, rel: str, body: str = "body") -> Path:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\nname: n\ntype: project\n---\n\n{body}\n", encoding="utf-8")
    return p


def test_relocates_proposed_sibling_into_inbox(tmp_path: Path) -> None:
    live = _page(tmp_path, "shared/project/a__x.md", body="approved")
    draft = _page(tmp_path, "shared/project/a__x.proposed.md", body="proposed")

    rep = proposed.relocate_proposed(tmp_path)

    moved = tmp_path / "shared" / "_inbox" / "project" / "a__x.proposed.md"
    assert rep.moved == 1
    assert moved.read_text(encoding="utf-8").endswith("proposed\n")
    assert not draft.exists()
    # The rule it shadowed is untouched.
    assert live.read_text(encoding="utf-8").endswith("approved\n")


def test_relocates_update_proposed_siblings_too(tmp_path: Path) -> None:
    """``.update-proposed.md`` is the same mechanism from ``inbox_flow``."""
    _page(tmp_path, "shared/feedback/use-yarn.update-proposed.md")
    rep = proposed.relocate_proposed(tmp_path)
    assert rep.moved == 1
    assert (tmp_path / "shared" / "_inbox" / "feedback" / "use-yarn.update-proposed.md").exists()


def test_leaves_correctly_staged_siblings_alone(tmp_path: Path) -> None:
    """A sibling already under ``_inbox`` is where it belongs."""
    staged = _page(tmp_path, "shared/_inbox/reference/r.proposed.md")
    rep = proposed.relocate_proposed(tmp_path)
    assert rep.moved == 0
    assert staged.exists()


def test_leaves_ordinary_rules_alone(tmp_path: Path) -> None:
    live = _page(tmp_path, "shared/project/a__x.md")
    rep = proposed.relocate_proposed(tmp_path)
    assert rep.moved == 0
    assert live.exists()


def test_dry_run_reports_without_moving(tmp_path: Path) -> None:
    draft = _page(tmp_path, "shared/project/a__x.proposed.md")
    rep = proposed.relocate_proposed(tmp_path, dry_run=True)
    assert rep.moved == 1
    assert draft.exists()
    assert not (tmp_path / "shared" / "_inbox" / "project" / "a__x.proposed.md").exists()


def test_does_not_clobber_an_existing_staged_sibling(tmp_path: Path) -> None:
    """A sibling may already be staged from an earlier run. Overwriting it
    would discard a proposal the user has not reviewed yet, so the stray copy
    is left in place and reported as skipped — losing a proposal is worse than
    leaving one misfiled for another run."""
    _page(tmp_path, "shared/_inbox/project/a__x.proposed.md", body="older proposal")
    stray = _page(tmp_path, "shared/project/a__x.proposed.md", body="newer proposal")

    rep = proposed.relocate_proposed(tmp_path)

    assert rep.moved == 0
    assert len(rep.skipped) == 1
    assert stray.exists()
    staged = tmp_path / "shared" / "_inbox" / "project" / "a__x.proposed.md"
    assert staged.read_text(encoding="utf-8").endswith("older proposal\n")


def test_empty_vault_is_a_no_op(tmp_path: Path) -> None:
    rep = proposed.relocate_proposed(tmp_path)
    assert rep.moved == 0
    assert rep.skipped == []


def test_doctor_reports_stray_proposed_siblings(tmp_path: Path, capsys) -> None:
    """Doctor reports, the migration moves — same split as missing_slugs."""
    from mnemo.cli.commands.doctor_checks import rules as doctor_rules

    _page(tmp_path, "shared/project/a__x.md")
    stray = _page(tmp_path, "shared/project/a__x.proposed.md")

    assert doctor_rules._doctor_check_stray_proposed(tmp_path) is True

    out = capsys.readouterr().out
    assert "1 staged rewrite" in out
    # Dry run: doctor never moves files.
    assert stray.exists()


def test_doctor_is_quiet_when_no_strays(tmp_path: Path, capsys) -> None:
    from mnemo.cli.commands.doctor_checks import rules as doctor_rules

    _page(tmp_path, "shared/project/a__x.md")
    _page(tmp_path, "shared/_inbox/project/b__y.proposed.md")

    assert doctor_rules._doctor_check_stray_proposed(tmp_path) is True
    assert "no staged rewrites in the live rule dirs" in capsys.readouterr().out
