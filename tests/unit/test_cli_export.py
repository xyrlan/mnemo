from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo import cli
from tests.unit._export_fixtures import write_rule


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
    return tmp_vault


def test_export_is_registered_with_its_flags():
    from mnemo.cli.parser import ADVANCED_COMMANDS, COMMANDS, _build_parser
    import mnemo.cli.commands  # noqa: F401

    assert "export" in COMMANDS and "export" not in ADVANCED_COMMANDS
    ns = _build_parser().parse_args(["export", "--host", "cursor", "--limit", "3", "--dry-run"])
    assert ns.host == "cursor" and ns.limit == 3 and ns.dry_run is True
    ns = _build_parser().parse_args(["export"])
    assert ns.host == "claude" and ns.target == "auto" and ns.types == "feedback,user"


def test_dry_run_prints_block_and_writes_nothing(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="use-yarn-not-npm", name="Use yarn", quote="always yarn")
    assert cli.main(["export", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "### Use yarn  `use-yarn-not-npm`" in out
    assert "would write 1 rule" in out
    assert not (repo / ".claude" / "rules" / "mnemo.md").exists()
    assert not (vault / ".mnemo" / "export" / "app.json").exists()


def test_export_writes_file_manifest_and_summary(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="use-yarn-not-npm", name="Use yarn", quote="always yarn")
    write_rule(vault, slug="uni", projects=("x", "y"))
    assert cli.main(["export"]) == 0
    out = capsys.readouterr().out
    assert "exported 2 rules (1 universal) → .claude/rules/mnemo.md" in out
    text = (repo / ".claude" / "rules" / "mnemo.md").read_text(encoding="utf-8")
    assert text.startswith("<!-- mnemo:start") and '> you said: "always yarn"' in text
    data = json.loads((vault / ".mnemo" / "export" / "app.json").read_text())
    assert data["cwd"] == str(repo.resolve()) and set(data["rules"]) == {"use-yarn-not-npm", "uni"}
    assert data["path"] == ".claude/rules/mnemo.md"


def test_no_rules_says_so_and_writes_nothing(repo: Path, vault: Path, capsys):
    assert cli.main(["export"]) == 0
    out = capsys.readouterr().out
    assert "no rules to export for app" in out and "mnemo learn" in out
    assert not (repo / ".claude" / "rules" / "mnemo.md").exists()


def test_cursor_and_codex_targets(repo: Path, vault: Path):
    write_rule(vault, slug="r")
    (repo / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    assert cli.main(["export", "--host", "cursor"]) == 0
    assert (repo / ".cursor" / "rules" / "mnemo.mdc").read_text().startswith("---\ndescription:")
    assert cli.main(["export", "--host", "codex"]) == 0
    agents = (repo / "AGENTS.md").read_text()
    assert agents.startswith("# Agents\n") and "<!-- mnemo:end -->" in agents


def test_mismatched_target_errors(repo: Path, vault: Path, capsys):
    assert cli.main(["export", "--host", "cursor", "--target", "claude-md"]) == 2
    assert "not a cursor target" in capsys.readouterr().err


def test_single_marker_refuses_without_writing(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="r")
    (repo / "CLAUDE.md").write_text("x\n<!-- mnemo:start — old -->\nhalf\n", encoding="utf-8")
    assert cli.main(["export", "--target", "claude-md"]) == 1
    assert "one mnemo marker" in capsys.readouterr().err
    assert (repo / "CLAUDE.md").read_text() == "x\n<!-- mnemo:start — old -->\nhalf\n"


def test_remove_strips_file_and_manifest(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="r")
    cli.main(["export"])
    assert cli.main(["export", "--remove"]) == 0
    assert "removed .claude/rules/mnemo.md" in capsys.readouterr().out
    assert not (repo / ".claude" / "rules" / "mnemo.md").exists()
    assert not (vault / ".mnemo" / "export" / "app.json").exists()


def test_token_warning_names_limit(repo: Path, vault: Path, capsys):
    for i in range(40):
        write_rule(vault, slug=f"r{i:02d}", body=("word " * 120) + "\n")
    cli.main(["export", "--dry-run"])
    err = capsys.readouterr().err
    assert "tokens" in err and "--limit" in err


def test_all_types_includes_reference_and_always_warns(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="ref", page_type="reference")
    assert cli.main(["export", "--all-types", "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert "`ref`" in captured.out and "--limit" in captured.err


def test_project_override(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="theirs", projects=("other",))
    cli.main(["export", "--project", "other", "--dry-run"])
    assert "`theirs`" in capsys.readouterr().out


def test_unreadable_target_is_a_clean_error(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="r")
    (repo / "CLAUDE.md").write_bytes("caf\xe9\n".encode("latin-1"))
    assert cli.main(["export", "--target", "claude-md"]) == 1
    assert "not valid UTF-8" in capsys.readouterr().err
