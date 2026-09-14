"""``mnemo publish`` — the CLI surface: flags, lines, exit codes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit import _share_stub
from tests.unit._export_fixtures import write_rule

_share_stub.ensure_format_module()

from mnemo import cli  # noqa: E402
# Registration happens at import; ``cli/commands/__init__.py`` is outside the
# publish piece's file boundary (see the contract), so the landing adds the
# import there and this test registers the command itself until then.
import mnemo.cli.commands.publish  # noqa: E402,F401
from mnemo.core.share import format as share_format  # noqa: E402

SHARE_DIR = share_format.SHARE_DIR


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo named ``app`` as the cwd, so the project resolves to ``app``."""
    root = tmp_path / "app"
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def vault(tmp_vault: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(tmp_vault)})
    monkeypatch.setattr("mnemo.core.share.publish.share_dir_is_gitignored", lambda repo_root: False)
    return tmp_vault


def _tree(repo: Path) -> set[str]:
    root = repo / SHARE_DIR
    return {p.relative_to(root).as_posix() for p in root.rglob("*.md")} if root.is_dir() else set()


def test_publish_is_registered_with_its_flags():
    from mnemo.cli.parser import ADVANCED_COMMANDS, COMMANDS, INTERNAL_COMMANDS, _build_parser

    assert "publish" in COMMANDS
    assert "publish" not in ADVANCED_COMMANDS and "publish" not in INTERNAL_COMMANDS
    ns = _build_parser().parse_args(["publish", "--types", "feedback,project", "--dry-run"])
    assert ns.types == "feedback,project" and ns.dry_run is True and ns.remove is False
    ns = _build_parser().parse_args(["publish"])
    assert ns.types == "feedback" and ns.project is None and ns.dry_run is False


def test_first_publish_writes_tree_manifest_and_lines(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="use-yarn", name="Use yarn", quote="always yarn")
    write_rule(vault, slug="uni", projects=("x", "y"))
    write_rule(vault, slug="me", page_type="user")

    assert cli.main(["publish"]) == 0

    out, err = capsys.readouterr()
    assert f"published 2 rules (1 universal) → {SHARE_DIR}: 2 new, 0 updated, 0 unchanged, 0 pruned" in out
    assert f"commit {SHARE_DIR} and push" in out and "mnemo import" in out
    assert "mnemo status" in out
    assert err == ""
    assert _tree(repo) == {"feedback/use-yarn.md", "feedback/uni.md"}
    data = json.loads((vault / ".mnemo" / "share" / "app.json").read_text(encoding="utf-8"))
    assert data["cwd"] == str(repo.resolve()) and set(data["rules"]) == {"use-yarn", "uni"}


def test_rerun_says_nothing_changed(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="a")
    assert cli.main(["publish"]) == 0
    capsys.readouterr()
    assert cli.main(["publish"]) == 0
    out = capsys.readouterr().out
    assert "0 new, 0 updated, 1 unchanged, 0 pruned" in out
    assert "nothing changed since the last publish" in out
    assert "commit" not in out


def test_dry_run_lists_the_plan_and_writes_nothing(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="a")
    assert cli.main(["publish"]) == 0
    write_rule(vault, slug="a", body="changed\n")
    write_rule(vault, slug="b")
    (vault / "shared" / "feedback").joinpath("a.md")  # still there; only b is new
    manifest_before = (vault / ".mnemo" / "share" / "app.json").read_bytes()
    capsys.readouterr()

    assert cli.main(["publish", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert f"would update {SHARE_DIR}/feedback/a.md" in out
    assert f"would write {SHARE_DIR}/feedback/b.md" in out
    assert "would publish 2 rules (0 universal)" in out and "1 new, 1 updated" in out
    assert _tree(repo) == {"feedback/a.md"}
    assert (vault / ".mnemo" / "share" / "app.json").read_bytes() == manifest_before


def test_prune_is_reported(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="a")
    write_rule(vault, slug="gone")
    assert cli.main(["publish"]) == 0
    (vault / "shared" / "feedback" / "gone.md").unlink()
    capsys.readouterr()
    assert cli.main(["publish", "--dry-run"]) == 0
    assert f"would prune {SHARE_DIR}/feedback/gone.md" in capsys.readouterr().out
    assert cli.main(["publish"]) == 0
    out = capsys.readouterr().out
    assert "1 unchanged, 1 pruned" in out and _tree(repo) == {"feedback/a.md"}


def test_refused_collision_is_a_counted_line_with_exit_0(repo: Path, vault: Path, capsys):
    theirs = share_format.to_portable(
        "---\nname: 'A'\nslug: a\ndescription: 'x'\ntype: feedback\nstability: stable\n"
        "sources:\n  - bots/app/briefings/sessions/t.md\ntags:\n  - auto-promoted\n---\n\nTheirs.\n",
        vault="feedfacefeedface", project="app", today="2026-01-01",
    )
    target = repo / SHARE_DIR / "feedback" / "a.md"
    target.parent.mkdir(parents=True)
    target.write_text(theirs, encoding="utf-8")
    write_rule(vault, slug="a")
    write_rule(vault, slug="b")

    assert cli.main(["publish"]) == 0

    out = capsys.readouterr().out
    assert f"refused {SHARE_DIR}/feedback/a.md: published by another vault" in out
    assert "1 rule (0 universal)" in out and "1 refused" in out
    assert target.read_text(encoding="utf-8") == theirs


def test_no_rules_says_so(repo: Path, vault: Path, capsys):
    assert cli.main(["publish"]) == 0
    out = capsys.readouterr().out
    assert "no rules to publish for app" in out and "mnemo learn" in out
    assert not (repo / SHARE_DIR).exists()


def test_user_pages_print_the_export_caveat(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="me", page_type="user")
    assert cli.main(["publish", "--types", "feedback,user"]) == 0
    err = capsys.readouterr().err
    assert "1 user-profile page(s) included (me)" in err and "names or emails" in err


def test_bad_types_are_usage_errors(repo: Path, vault: Path, capsys):
    assert cli.main(["publish", "--types", "feedback,bogus"]) == 2
    assert "unknown page type(s): bogus" in capsys.readouterr().err
    assert cli.main(["publish", "--types", " , "]) == 2
    assert "--types cannot be empty" in capsys.readouterr().err


def test_gitignored_tree_warns_on_stderr(repo: Path, vault: Path, capsys, monkeypatch):
    write_rule(vault, slug="a")
    monkeypatch.setattr("mnemo.core.share.publish.share_dir_is_gitignored", lambda repo_root: True)
    assert cli.main(["publish"]) == 0
    err = capsys.readouterr().err
    assert f"warning: {SHARE_DIR} is ignored by this repo's .gitignore" in err


def test_stray_files_are_noted_on_stderr(repo: Path, vault: Path, capsys):
    stray = repo / SHARE_DIR / "feedback" / "README.md"
    stray.parent.mkdir(parents=True)
    stray.write_text("# hi\n", encoding="utf-8")
    write_rule(vault, slug="a")
    assert cli.main(["publish"]) == 0
    err = capsys.readouterr().err
    assert f"1 file(s) in {SHARE_DIR} are not mnemo pages, left alone: feedback/README.md" in err
    assert stray.exists()


def test_remove_deletes_ours_and_manifest(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="a")
    assert cli.main(["publish"]) == 0
    capsys.readouterr()

    assert cli.main(["publish", "--remove"]) == 0
    out = capsys.readouterr().out
    assert f"removed {SHARE_DIR}/feedback/a.md" in out and "removed 1 file of this vault's" in out
    assert not (repo / SHARE_DIR).exists()
    assert not (vault / ".mnemo" / "share" / "app.json").exists()

    assert cli.main(["publish", "--remove"]) == 0
    assert f"nothing to remove at {SHARE_DIR}" in capsys.readouterr().out


def test_write_failure_is_exit_1(repo: Path, vault: Path, capsys, monkeypatch):
    write_rule(vault, slug="a")

    def boom(path, data):
        raise PermissionError(13, "read-only", str(path))

    monkeypatch.setattr("mnemo.core.share.publish.atomic_write_bytes", boom)
    assert cli.main(["publish"]) == 1
    assert f"error: could not write {SHARE_DIR}" in capsys.readouterr().err


def test_project_flag_selects_another_projects_rules(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="theirs", projects=("other",))
    assert cli.main(["publish", "--project", "other"]) == 0
    assert _tree(repo) == {"feedback/theirs.md"}, "the tree still lands in the cwd's repo"
    assert (vault / ".mnemo" / "share" / "other.json").exists()
