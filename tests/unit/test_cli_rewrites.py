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
