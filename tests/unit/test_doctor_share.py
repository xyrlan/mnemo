"""``mnemo doctor`` — the published-tree row: silent unless behind."""
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


def test_registered_in_the_doctor_table():
    from mnemo.cli.commands import doctor
    from mnemo.cli.commands.doctor_checks import _doctor_check_share_published, share

    names = [name for name, _ in doctor.DOCTOR_CHECKS]
    assert "share_published" in names
    fn = dict(doctor.DOCTOR_CHECKS)["share_published"]
    assert fn is share._doctor_check_share_published is _doctor_check_share_published


def test_silent_when_never_published(vault: Path, repo: Path, capsys):
    from mnemo.cli.commands.doctor_checks.share import _doctor_check_share_published

    write_rule(vault, slug="a")
    assert _doctor_check_share_published(vault) is True
    assert capsys.readouterr().out == ""


def test_silent_when_up_to_date(vault: Path, repo: Path, capsys):
    from mnemo.cli.commands.doctor_checks.share import _doctor_check_share_published

    write_rule(vault, slug="a")
    pub.run_publish(vault, project="app", repo_root=repo)
    assert _doctor_check_share_published(vault) is True
    assert capsys.readouterr().out == ""


def test_warns_when_behind(vault: Path, repo: Path, capsys):
    from mnemo.cli.commands.doctor_checks.share import _doctor_check_share_published

    write_rule(vault, slug="a")
    pub.run_publish(vault, project="app", repo_root=repo)
    write_rule(vault, slug="b")

    assert _doctor_check_share_published(vault) is False
    out = capsys.readouterr().out
    assert f"⚠ Published rules: 1 differ from the vault now (1 rule in {SHARE_DIR})" in out
    assert "→ run `mnemo publish`" in out and f"commit {SHARE_DIR}" in out


def test_check_is_stateless(vault: Path, repo: Path, capsys):
    """Same answer on every run — no once-ever notice."""
    from mnemo.cli.commands.doctor_checks.share import _doctor_check_share_published

    write_rule(vault, slug="a")
    pub.run_publish(vault, project="app", repo_root=repo)
    write_rule(vault, slug="b")
    assert _doctor_check_share_published(vault) is False
    assert _doctor_check_share_published(vault) is False
    assert capsys.readouterr().out.count("⚠ Published rules") == 2


def test_survives_a_broken_staleness(vault: Path, repo: Path, capsys, monkeypatch):
    from mnemo.cli.commands.doctor_checks.share import _doctor_check_share_published

    write_rule(vault, slug="a")
    pub.run_publish(vault, project="app", repo_root=repo)

    def boom(*a, **kw):
        raise RuntimeError("tree unreadable")

    monkeypatch.setattr(pub, "staleness", boom)
    assert _doctor_check_share_published(vault) is True
    assert capsys.readouterr().out == ""


def test_doctor_command_carries_the_warning(vault: Path, repo: Path, capsys, monkeypatch):
    from mnemo import cli

    monkeypatch.setattr(cli, "_resolve_vault", lambda: vault)
    write_rule(vault, slug="a")
    pub.run_publish(vault, project="app", repo_root=repo)
    write_rule(vault, slug="b")
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "⚠ Published rules: 1 differ from the vault now" in out
