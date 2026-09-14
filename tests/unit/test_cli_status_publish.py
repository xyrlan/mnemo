"""``mnemo status`` — the one line about the published tree."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit import _share_stub
from tests.unit._export_fixtures import write_rule

_share_stub.ensure_format_module()

from mnemo.core.share import format as share_format  # noqa: E402
from mnemo.core.share import publish as pub  # noqa: E402

SHARE_DIR = share_format.SHARE_DIR


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "app"
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def vault(tmp_vault: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(tmp_vault)})
    return tmp_vault


def test_never_published_prints_nothing(vault: Path, repo: Path, capsys):
    from mnemo.cli.commands.status import _print_publish_status

    write_rule(vault, slug="a")
    _print_publish_status(vault)
    assert capsys.readouterr().out == ""


def test_up_to_date_and_behind(vault: Path, repo: Path, capsys):
    from mnemo.cli.commands.status import _print_publish_status

    write_rule(vault, slug="a")
    pub.run_publish(vault, project="app", repo_root=repo)

    _print_publish_status(vault)
    assert capsys.readouterr().out == f"\nPublished: 1 rule → {SHARE_DIR} (up to date)\n"

    write_rule(vault, slug="a", body="changed\n")
    write_rule(vault, slug="b")
    _print_publish_status(vault)
    assert capsys.readouterr().out == (
        f"\nPublished: 1 rule → {SHARE_DIR} (2 differ from the vault now, run mnemo publish)\n"
    )


def test_status_command_prints_it_after_the_export_line(vault: Path, repo: Path, capsys, monkeypatch):
    from mnemo import cli
    from mnemo.core import export as export_mod

    monkeypatch.setattr(cli, "_resolve_vault", lambda: vault)
    write_rule(vault, slug="a")
    export_mod.run_export(vault, project="app", repo_root=repo)
    pub.run_publish(vault, project="app", repo_root=repo)

    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    export_line = out.index("Export: 1 rule → .claude/rules/mnemo.md (up to date)")
    publish_line = out.index(f"Published: 1 rule → {SHARE_DIR} (up to date)")
    assert export_line < publish_line


def test_status_line_survives_a_broken_staleness(vault: Path, repo: Path, capsys, monkeypatch):
    """A status line is not worth a traceback."""
    from mnemo.cli.commands.status import _print_publish_status

    write_rule(vault, slug="a")
    pub.run_publish(vault, project="app", repo_root=repo)

    def boom(*a, **kw):
        raise RuntimeError("tree unreadable")

    monkeypatch.setattr(pub, "staleness", boom)
    _print_publish_status(vault)
    assert capsys.readouterr().out == ""
