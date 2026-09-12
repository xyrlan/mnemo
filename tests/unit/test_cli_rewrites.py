"""``mnemo rewrites`` — review surface for staged ``.proposed.md`` rewrites (#159)."""
from __future__ import annotations

import argparse
from pathlib import Path

from mnemo.cli.parser import ADVANCED_COMMANDS, COMMANDS, _build_parser


def test_rewrites_registered_as_advanced_command():
    import mnemo.cli.commands  # noqa: F401 — populates COMMANDS
    assert "rewrites" in COMMANDS and "rewrites" in ADVANCED_COMMANDS
    ns = _build_parser().parse_args(["rewrites", "--apply-safe"])
    assert ns.command == "rewrites" and ns.apply_safe is True
    ns = _build_parser().parse_args(["rewrites", "--show", "project/a__x"])
    assert ns.show == "project/a__x"
    ns = _build_parser().parse_args(["rewrites", "--undo", "20260912T000000"])
    assert ns.undo == "20260912T000000"


def _seed(vault: Path) -> None:
    fm = "name: n\nslug: a__x\ntype: project\nsources:\n  - bots/a/memory/a__x.md"
    live = vault / "shared" / "project" / "a__x.md"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text(f"---\n{fm}\n---\n\nline one\n", encoding="utf-8")
    prop = vault / "shared" / "_inbox" / "project" / "a__x.proposed.md"
    prop.parent.mkdir(parents=True, exist_ok=True)
    prop.write_text(f"---\n{fm}\n---\n\nline one\nline two\n", encoding="utf-8")


def test_two_write_actions_are_refused_rather_than_resolved_by_precedence(
    tmp_vault: Path, monkeypatch, capsys
):
    """``--accept X --reject X`` must not silently pick one.

    The dispatch resolves conflicts by precedence, so reject ran and the accept
    was never mentioned. Tolerable for flags that only print; not for two that
    write.
    """
    from mnemo import cli
    from mnemo.cli.commands import rewrites as cmd

    _seed(tmp_vault)
    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    args = argparse.Namespace(
        command="rewrites", apply_safe=False, show=None,
        accept="project/a__x", reject="project/a__x", undo=None,
    )

    assert cmd.cmd_rewrites(args) == 1

    out = capsys.readouterr().out
    assert "pick one action" in out
    # Nothing was written: the proposal survives.
    assert (tmp_vault / "shared" / "_inbox" / "project" / "a__x.proposed.md").exists()


def test_reject_archives_the_proposal_before_deleting_it(tmp_vault: Path, monkeypatch, capsys):
    """A reject must not be the one path that destroys reviewed text.

    ``apply`` archives every file it touches. ``--reject`` only unlinked, and the
    extractor re-derives a proposal from its source — so once that source moves
    on, the rejected text was gone for good.
    """
    from mnemo import cli
    from mnemo.cli.commands import rewrites as cmd

    _seed(tmp_vault)
    prop = tmp_vault / "shared" / "_inbox" / "project" / "a__x.proposed.md"
    before = prop.read_bytes()
    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    args = argparse.Namespace(
        command="rewrites", apply_safe=False, show=None,
        accept=None, reject="project/a__x", undo=None,
    )

    assert cmd.cmd_rewrites(args) == 0
    assert not prop.exists()

    archived = list((tmp_vault / "shared" / "_archive").glob("rejected-*/a__x.proposed.md"))
    assert len(archived) == 1
    assert archived[0].read_bytes() == before
    assert "archived to" in capsys.readouterr().out


def test_unreadable_proposal_reports_a_message_not_a_traceback(
    tmp_vault: Path, monkeypatch, capsys
):
    """A hidden backlog surfaced as a stack trace is still hidden.

    ``classify`` raises on an unreadable proposal rather than silently shrinking
    the plan — that guarantee is pinned in the classify tests. But letting the
    raise escape ``cmd_rewrites`` printed a bare ``PermissionError`` traceback,
    which tells a human something broke without saying which file or why. The
    boundary catches it and names the path.
    """
    import os

    from mnemo import cli
    from mnemo.cli.commands import rewrites as cmd

    _seed(tmp_vault)
    prop = tmp_vault / "shared" / "_inbox" / "project" / "a__x.proposed.md"
    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    args = argparse.Namespace(
        command="rewrites", apply_safe=False, show=None, accept=None, reject=None, undo=None
    )

    os.chmod(prop, 0o000)
    try:
        assert cmd.cmd_rewrites(args) == 1
    finally:
        os.chmod(prop, 0o644)

    out = capsys.readouterr().out
    assert "cannot read" in out
    assert "a__x.proposed.md" in out


def test_dry_run_lists_safe_and_undecided_without_writing(tmp_vault: Path, monkeypatch, capsys):
    from mnemo import cli
    from mnemo.cli.commands import rewrites as cmd

    _seed(tmp_vault)
    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    args = argparse.Namespace(
        command="rewrites", apply_safe=False, show=None, accept=None, reject=None, undo=None
    )

    assert cmd.cmd_rewrites(args) == 0

    out = capsys.readouterr().out
    assert "1 staged rewrite" in out
    assert "safe to merge (1)" in out
    assert "--apply-safe" in out
    # Nothing was written.
    assert (tmp_vault / "shared" / "_inbox" / "project" / "a__x.proposed.md").exists()
