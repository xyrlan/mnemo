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
    data = json.loads((vault / ".mnemo" / "export" / "app.json").read_text(encoding="utf-8"))
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
    assert (repo / ".cursor" / "rules" / "mnemo.mdc").read_text(encoding="utf-8").startswith("---\ndescription:")
    assert cli.main(["export", "--host", "codex"]) == 0
    agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.startswith("# Agents\n") and "<!-- mnemo:end -->" in agents


def test_mismatched_target_errors(repo: Path, vault: Path, capsys):
    assert cli.main(["export", "--host", "cursor", "--target", "claude-md"]) == 2
    assert "not a cursor target" in capsys.readouterr().err


def test_single_marker_refuses_without_writing(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="r")
    (repo / "CLAUDE.md").write_text("x\n<!-- mnemo:start — old -->\nhalf\n", encoding="utf-8")
    assert cli.main(["export", "--target", "claude-md"]) == 1
    assert "one mnemo marker" in capsys.readouterr().err
    assert (repo / "CLAUDE.md").read_text(encoding="utf-8") == "x\n<!-- mnemo:start — old -->\nhalf\n"


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
    assert "tokens" in err and "--limit 20" in err


def test_token_warning_also_reaches_stderr_on_the_write_path(repo: Path, vault: Path, capsys):
    for i in range(40):
        write_rule(vault, slug=f"r{i:02d}", body=("word " * 120) + "\n")
    assert cli.main(["export"]) == 0
    err = capsys.readouterr().err
    assert "tokens" in err and "--limit 20" in err


def test_all_types_includes_reference_and_always_warns(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="ref", page_type="reference")
    assert cli.main(["export", "--all-types", "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert "`ref`" in captured.out and "noisier" in captured.err and "--limit" in captured.err


def test_project_override(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="theirs", projects=("other",))
    cli.main(["export", "--project", "other", "--dry-run"])
    assert "`theirs`" in capsys.readouterr().out


def test_unreadable_target_is_a_clean_error(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="r")
    (repo / "CLAUDE.md").write_bytes("caf\xe9\n".encode("latin-1"))
    assert cli.main(["export", "--target", "claude-md"]) == 1
    assert "not valid UTF-8" in capsys.readouterr().err


def test_empty_types_is_a_usage_error(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="r")
    assert cli.main(["export", "--types", " , ", "--dry-run"]) == 2
    assert "--types cannot be empty" in capsys.readouterr().err
    assert not (repo / ".claude" / "rules" / "mnemo.md").exists()
    assert not (vault / ".mnemo" / "export" / "app.json").exists()


def test_unknown_type_is_a_usage_error(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="r")
    assert cli.main(["export", "--types", "feedback,bogus"]) == 2
    err = capsys.readouterr().err
    assert "unknown page type(s): bogus" in err
    assert "feedback, user, reference, project" in err
    assert not (repo / ".claude" / "rules" / "mnemo.md").exists()
    assert not (vault / ".mnemo" / "export" / "app.json").exists()


def test_limit_below_one_is_a_usage_error(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="r")
    assert cli.main(["export", "--limit", "0"]) == 2
    assert "--limit must be at least 1" in capsys.readouterr().err
    assert not (repo / ".claude" / "rules" / "mnemo.md").exists()
    assert not (vault / ".mnemo" / "export" / "app.json").exists()


def test_all_types_with_explicit_types_is_a_usage_error(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="r")
    assert cli.main(["export", "--all-types", "--types", "feedback"]) == 2
    assert "--all-types and --types are mutually exclusive" in capsys.readouterr().err
    assert not (repo / ".claude" / "rules" / "mnemo.md").exists()
    assert not (vault / ".mnemo" / "export" / "app.json").exists()


def test_remove_on_claude_md_leaves_the_rest_byte_identical(repo: Path, vault: Path):
    write_rule(vault, slug="r")
    original = "# My project\n\nsome notes here\n"
    (repo / "CLAUDE.md").write_text(original, encoding="utf-8")
    assert cli.main(["export", "--target", "claude-md"]) == 0
    assert cli.main(["export", "--target", "claude-md", "--remove"]) == 0
    assert (repo / "CLAUDE.md").read_text(encoding="utf-8") == original


def test_export_twice_is_byte_identical(repo: Path, vault: Path):
    write_rule(vault, slug="use-yarn-not-npm", name="Use yarn", quote="always yarn")
    write_rule(vault, slug="uni", projects=("x", "y"))
    assert cli.main(["export"]) == 0
    first_text = (repo / ".claude" / "rules" / "mnemo.md").read_bytes()
    first_manifest = json.loads((vault / ".mnemo" / "export" / "app.json").read_text(encoding="utf-8"))

    assert cli.main(["export"]) == 0
    second_text = (repo / ".claude" / "rules" / "mnemo.md").read_bytes()
    second_manifest = json.loads((vault / ".mnemo" / "export" / "app.json").read_text(encoding="utf-8"))

    assert first_text == second_text
    assert first_manifest["rules"] == second_manifest["rules"]


def test_user_page_note_on_dry_run(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="feedback-rule", page_type="feedback")
    write_rule(vault, slug="alice-profile", page_type="user")
    assert cli.main(["export", "--dry-run"]) == 0
    err = capsys.readouterr().err
    assert "note:" in err
    assert "1 user-profile page(s) included (alice-profile)" in err
    assert "--types feedback" in err


def test_user_page_note_on_write(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="feedback-rule", page_type="feedback")
    write_rule(vault, slug="alice-profile", page_type="user")
    assert cli.main(["export"]) == 0
    err = capsys.readouterr().err
    assert "1 user-profile page(s) included (alice-profile)" in err


def test_no_user_page_note_when_types_excludes_user(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="feedback-rule", page_type="feedback")
    write_rule(vault, slug="alice-profile", page_type="user")
    assert cli.main(["export", "--types", "feedback", "--dry-run"]) == 0
    err = capsys.readouterr().err
    assert "user-profile" not in err


def test_manifest_present_only_after_a_real_write(repo: Path, vault: Path):
    write_rule(vault, slug="r")
    manifest_path = vault / ".mnemo" / "export" / "app.json"

    assert cli.main(["export", "--dry-run"]) == 0
    assert not manifest_path.exists()

    assert cli.main(["export"]) == 0
    assert manifest_path.exists()

    assert cli.main(["export", "--remove"]) == 0
    assert not manifest_path.exists()


# --- compact default, --full opt-in (#129) ---------------------------------

_LONG_BODY = (
    "Use yarn in this repo.\n"
    "\n"
    "**Why:** npm rewrites the lockfile.\n"
    "\n"
    "**How to apply:** run `yarn add`.\n"
)


def test_full_flag_is_registered():
    from mnemo.cli.parser import _build_parser
    import mnemo.cli.commands  # noqa: F401

    assert _build_parser().parse_args(["export"]).full is False
    assert _build_parser().parse_args(["export", "--full"]).full is True


def test_export_is_compact_by_default(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="use-yarn-not-npm", name="Use yarn", body=_LONG_BODY, quote="always yarn")
    assert cli.main(["export"]) == 0
    text = (repo / ".claude" / "rules" / "mnemo.md").read_text(encoding="utf-8")
    assert "Use yarn in this repo." in text
    assert "**Why:**" not in text and "**How to apply:**" not in text
    assert '> you said: "always yarn"' in text
    assert "read_mnemo_rule" in text
    data = json.loads((vault / ".mnemo" / "export" / "app.json").read_text(encoding="utf-8"))
    assert data["format"] == "compact"


def test_full_flag_writes_the_whole_body(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="use-yarn-not-npm", name="Use yarn", body=_LONG_BODY, quote="always yarn")
    assert cli.main(["export", "--full"]) == 0
    text = (repo / ".claude" / "rules" / "mnemo.md").read_text(encoding="utf-8")
    assert "**Why:**" in text and "**How to apply:**" in text
    assert "read_mnemo_rule" not in text
    data = json.loads((vault / ".mnemo" / "export" / "app.json").read_text(encoding="utf-8"))
    assert data["format"] == "full"


def test_compact_export_of_long_rules_stays_under_the_warning(repo: Path, vault: Path, capsys):
    """Forty rules whose Why/How run long: full would warn, compact must not."""
    for i in range(40):
        write_rule(vault, slug=f"r{i:02d}", body="Short lead.\n\n" + ("word " * 120) + "\n")
    assert cli.main(["export", "--dry-run"]) == 0
    assert "tokens" not in capsys.readouterr().err
    assert cli.main(["export", "--full", "--dry-run"]) == 0
    assert "tokens" in capsys.readouterr().err


def test_full_mode_warning_suggests_dropping_full_first(repo: Path, vault: Path, capsys):
    for i in range(40):
        write_rule(vault, slug=f"r{i:02d}", body="Short lead.\n\n" + ("word " * 120) + "\n")
    assert cli.main(["export", "--full", "--dry-run"]) == 0
    err = capsys.readouterr().err
    assert "without --full" in err and "--limit 20" in err


def test_init_host_export_is_compact(repo: Path, vault: Path, capsys, monkeypatch):
    """``mnemo init --host`` goes through run_export with its defaults."""
    from mnemo.core import export as export_mod

    write_rule(vault, slug="r", body=_LONG_BODY)
    report = export_mod.run_export(vault, project="app", repo_root=repo, host="cursor")
    assert report.full is False
    assert "**Why:**" not in report.block
    assert "**Why:**" in export_mod.run_export(
        vault, project="app", repo_root=repo, host="cursor", full=True, dry_run=True,
    ).block
