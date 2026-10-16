"""``mnemo export --host grok``: the rules file Grok loads (``.grok/rules/*.md``)."""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo import cli
from tests.unit._export_fixtures import write_rule

BLOCK = "<!-- mnemo:start — x -->\n## Rules\n\n### A  `a`\nbody\n<!-- mnemo:end -->\n"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "app"
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def vault(tmp_vault: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(tmp_vault)})
    return tmp_vault


def test_grok_default_target_is_its_own_rules_file(tmp_path: Path):
    from mnemo.core.export.writers import target_for

    t = target_for("grok", "auto", tmp_path)
    assert t.path == tmp_path / ".grok" / "rules" / "mnemo.md"
    assert t.kind == "whole" and t.prelude == "" and t.host == "grok" and t.name == "rules"


def test_grok_can_target_agents_md_as_a_managed_block(tmp_path: Path):
    from mnemo.core.export.writers import target_for, write_target

    t = target_for("grok", "agents-md", tmp_path)
    assert t.path == tmp_path / "AGENTS.md" and t.kind == "block"
    t.path.write_text("# Mine\n\nkeep this\n", encoding="utf-8")
    write_target(t, BLOCK)
    text = t.path.read_text(encoding="utf-8")
    assert text.startswith("# Mine\n\nkeep this\n") and BLOCK in text


def test_grok_has_no_claude_md_target(tmp_path: Path):
    from mnemo.core.export.writers import TargetError, target_for

    with pytest.raises(TargetError):
        target_for("grok", "claude-md", tmp_path)


def test_the_grok_host_hands_out_the_same_target(tmp_path: Path):
    from mnemo.hosts import get_host

    assert get_host("grok").export_target(tmp_path).path == tmp_path / ".grok" / "rules" / "mnemo.md"


def test_the_other_hosts_targets_are_unchanged(tmp_path: Path):
    from mnemo.core.export.writers import target_for

    assert target_for("claude", "auto", tmp_path).path == tmp_path / ".claude" / "rules" / "mnemo.md"
    assert target_for("cursor", "auto", tmp_path).path == tmp_path / ".cursor" / "rules" / "mnemo.mdc"
    assert target_for("codex", "auto", tmp_path).path == tmp_path / "AGENTS.md"


def test_export_host_grok_writes_removes_and_records_the_host(repo: Path, vault: Path, capsys):
    from mnemo.core.export import manifest

    write_rule(vault, slug="use-yarn-not-npm", name="Use yarn", quote="always yarn")
    assert cli.main(["export", "--host", "grok"]) == 0
    out = capsys.readouterr().out
    rules = repo / ".grok" / "rules" / "mnemo.md"
    assert "exported 1 rule" in out and ".grok/rules/mnemo.md" in out
    assert "### Use yarn  `use-yarn-not-npm`" in rules.read_text(encoding="utf-8")
    assert manifest.read_manifest(vault, "app")["host"] == "grok"

    assert cli.main(["export", "--host", "grok", "--remove"]) == 0
    assert not rules.exists()
    assert manifest.read_manifest(vault, "app") is None


def test_export_host_grok_agents_md_keeps_the_users_text(repo: Path, vault: Path):
    write_rule(vault, slug="use-yarn-not-npm", name="Use yarn", quote="always yarn")
    (repo / "AGENTS.md").write_text("# Mine\n", encoding="utf-8")
    assert cli.main(["export", "--host", "grok", "--target", "agents-md"]) == 0
    text = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith("# Mine\n") and "use-yarn-not-npm" in text
    assert not (repo / ".grok").exists()


def test_a_grok_export_is_not_what_claude_loads(repo: Path, vault: Path):
    """The reflex skips rules a Claude file already carries; a Grok file is no such file."""
    from mnemo.core.export import manifest

    write_rule(vault, slug="use-yarn-not-npm", name="Use yarn", quote="always yarn")
    assert cli.main(["export", "--host", "grok"]) == 0
    assert manifest.exported_slugs_for(vault, "app", repo_root=str(repo)) == set()
