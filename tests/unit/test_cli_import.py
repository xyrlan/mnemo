"""``mnemo import``: one line per decision, a summary, and exit codes a script can read."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit._export_fixtures import write_rule
from tests.unit._share_stub import ensure_format, publish_rule

fmt = ensure_format()

from mnemo import cli  # noqa: E402
from mnemo.cli.commands import import_rules as cmd  # noqa: E402, F401  — registers @command("import")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo named ``app`` as the cwd, so the project resolves to ``app``."""
    root = tmp_path / "app"
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def vault(tmp_vault: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_vault / ".mnemo").mkdir(exist_ok=True)
    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(tmp_vault)})
    return tmp_vault


def test_import_is_registered_with_its_flags():
    from mnemo.cli.parser import ADVANCED_COMMANDS, COMMANDS, INTERNAL_COMMANDS, _build_parser

    assert "import" in COMMANDS
    assert "import" not in ADVANCED_COMMANDS and "import" not in INTERNAL_COMMANDS
    ns = _build_parser().parse_args(["import"])
    assert ns.path is None and ns.dry_run is False
    ns = _build_parser().parse_args(["import", "/some/where", "--dry-run"])
    assert ns.path == "/some/where" and ns.dry_run is True


def test_default_path_is_the_repos_tree_and_project_is_the_local_name(repo, vault, capsys):
    tree = repo / fmt.SHARE_DIR
    publish_rule(tree, slug="use-yarn", vault="other-vault", project="their-clone")

    assert cli.main(["import"]) == 0

    out = capsys.readouterr().out
    assert "staged feedback/use-yarn → shared/_inbox/feedback/use-yarn.md" in out
    assert "import: 1 staged, 0 proposed, 0 unchanged, 0 yours, 0 refused, 0 not a page" in out
    assert "review shared/_inbox/" in out
    staged = (vault / "shared" / "_inbox" / "feedback" / "use-yarn.md").read_text(encoding="utf-8")
    from mnemo.core.filters import parse_frontmatter
    assert parse_frontmatter(staged).get("projects") == ["app"]


def test_explicit_path_is_honoured(repo, vault, tmp_path, capsys):
    tree = tmp_path / "sent-by-a-colleague"
    publish_rule(tree, slug="use-yarn", vault="other-vault")

    assert cli.main(["import", str(tree)]) == 0

    assert (vault / "shared" / "_inbox" / "feedback" / "use-yarn.md").is_file()
    assert f"from {tree}" in capsys.readouterr().out


def test_missing_tree_is_a_usage_error_that_names_publish(repo, vault, capsys):
    assert cli.main(["import"]) == 2
    err = capsys.readouterr().err
    assert "nothing published in this repo" in err and "mnemo publish" in err
    assert not (vault / "shared" / "_inbox").exists()


def test_bad_explicit_path_is_a_usage_error(repo, vault, tmp_path, capsys):
    assert cli.main(["import", str(tmp_path / "nope")]) == 2
    assert "is not a directory" in capsys.readouterr().err


def test_dry_run_prints_every_decision_and_writes_nothing(repo, vault, capsys):
    tree = repo / fmt.SHARE_DIR
    write_rule(vault, slug="live-one")
    write_rule(vault, slug="clash", page_type="reference")
    publish_rule(tree, slug="fresh", vault="other-vault")
    publish_rule(tree, slug="live-one", vault="other-vault")
    publish_rule(tree, slug="clash", vault="other-vault")
    publish_rule(tree, slug="mine", vault=fmt.vault_id(vault))
    (tree / "feedback" / "README.md").write_text("stray\n", encoding="utf-8")

    rc = cli.main(["import", "--dry-run"])

    out, err = capsys.readouterr()
    assert rc == 1, "a refused collision is the tree's problem, and exit 1 says so"
    assert "would stage feedback/fresh → shared/_inbox/feedback/fresh.md" in out
    assert "would propose feedback/live-one → shared/_inbox/feedback/live-one.proposed.md" in out
    assert "skip feedback/mine: published by this vault" in out
    assert "refused feedback/clash: slug already live under another type: shared/reference/clash.md" in err
    assert "skipped" in err and "README.md" in err
    assert "import (dry run): 1 staged, 1 proposed, 0 unchanged, 1 yours, 1 refused, 1 not a page" in out
    assert not (vault / "shared" / "_inbox").exists()
    assert not (vault / ".mnemo" / "share").exists()


def test_rerun_is_quiet_and_exit_zero(repo, vault, capsys):
    tree = repo / fmt.SHARE_DIR
    publish_rule(tree, slug="use-yarn", vault="other-vault")
    assert cli.main(["import"]) == 0
    capsys.readouterr()

    assert cli.main(["import"]) == 0

    out = capsys.readouterr().out
    assert "staged feedback/use-yarn" not in out
    assert "import: 0 staged, 0 proposed, 1 unchanged" in out
    assert "review shared/_inbox/" not in out


def test_refused_is_exit_one_but_the_rest_still_lands(repo, vault, capsys):
    tree = repo / fmt.SHARE_DIR
    write_rule(vault, slug="clash", page_type="reference")
    publish_rule(tree, slug="clash", vault="other-vault")
    publish_rule(tree, slug="fine", vault="other-vault")

    assert cli.main(["import"]) == 1

    assert (vault / "shared" / "_inbox" / "feedback" / "fine.md").is_file()
    assert not (vault / "shared" / "_inbox" / "feedback" / "clash.md").exists()
    assert "refused feedback/clash" in capsys.readouterr().err


def test_stray_file_alone_is_exit_zero(repo, vault, capsys):
    tree = repo / fmt.SHARE_DIR
    (tree / "feedback").mkdir(parents=True)
    (tree / "feedback" / "README.md").write_text("stray\n", encoding="utf-8")

    assert cli.main(["import"]) == 0
    assert "1 not a page" in capsys.readouterr().out


def test_failed_write_is_exit_one(repo, vault, monkeypatch, capsys):
    from mnemo.core.share import imports as imp

    tree = repo / fmt.SHARE_DIR
    publish_rule(tree, slug="use-yarn", vault="other-vault")

    def boom(path, data):
        raise PermissionError("read-only vault")

    monkeypatch.setattr(imp, "atomic_write_bytes", boom)

    assert cli.main(["import"]) == 1
    out, err = capsys.readouterr()
    assert "failed feedback/use-yarn: PermissionError: read-only vault" in err
    assert "1 failed" in out
