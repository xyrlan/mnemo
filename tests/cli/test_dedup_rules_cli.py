"""`mnemo dedup-rules` — dry-run default, prints plan; `--apply` writes."""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo import cli as cli_mod
from mnemo.cli.commands import dedup_rules as cmd_mod  # noqa: F401 (register)


def _seed(vault: Path) -> None:
    d = vault / "shared" / "feedback"
    d.mkdir(parents=True)
    for slug, ts in [("a", "2026-04-19T10:00:00"), ("b", "2026-04-20T10:00:00")]:
        (d / f"{slug}.md").write_text(
            f"---\nname: 'Dup'\ndescription: 'd'\ntype: feedback\n"
            f"extracted_at: {ts}\nstability: stable\n"
            f"sources:\n  - bots/x/{slug}.md\ntags: []\n---\nbody\n",
            encoding="utf-8",
        )


def test_dry_run_reports_without_touching(tmp_path, capsys, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setattr(cli_mod, "_resolve_vault", lambda: tmp_path)
    import argparse
    rc = cmd_mod.cmd_dedup_rules(argparse.Namespace(apply=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 group" in out and "Dup" in out and "a.md" in out
    assert (tmp_path / "shared" / "feedback" / "a.md").exists()


def test_apply_merges(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setattr(cli_mod, "_resolve_vault", lambda: tmp_path)
    import argparse
    rc = cmd_mod.cmd_dedup_rules(argparse.Namespace(apply=True))
    assert rc == 0
    assert not (tmp_path / "shared" / "feedback" / "a.md").exists()
    assert (tmp_path / "shared" / "feedback" / "b.md").exists()


def test_no_duplicates_is_clean_exit(tmp_path, capsys, monkeypatch):
    (tmp_path / "shared" / "feedback").mkdir(parents=True)
    monkeypatch.setattr(cli_mod, "_resolve_vault", lambda: tmp_path)
    import argparse
    rc = cmd_mod.cmd_dedup_rules(argparse.Namespace(apply=False))
    assert rc == 0
    assert "no duplicates" in capsys.readouterr().out.lower()
