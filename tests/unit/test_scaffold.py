from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.install import scaffold


def test_scaffold_creates_full_tree(tmp_path: Path):
    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    assert (vault / "HOME.md").exists()
    assert (vault / "README.md").exists()
    assert (vault / "mnemo.config.json").exists()
    assert (vault / ".obsidian" / "snippets" / "graph-dark-gold.css").exists()
    assert (vault / "bots").is_dir()
    # Auto-populated Tier 2 — created by mnemo extract, pre-created for
    # stable Obsidian navigation on day one.
    assert (vault / "shared" / "feedback").is_dir()
    assert (vault / "shared" / "user").is_dir()
    assert (vault / "shared" / "reference").is_dir()
    assert (vault / "shared" / "project").is_dir()
    # User-maintained Tier 2
    assert (vault / "shared" / "people").is_dir()
    assert (vault / "shared" / "companies").is_dir()
    assert (vault / "shared" / "decisions").is_dir()
    # Tier 3
    assert (vault / "wiki" / "sources").is_dir()
    assert (vault / "wiki" / "compiled").is_dir()


def test_scaffold_does_not_create_stale_plural_projects_dir(tmp_path: Path):
    """The extraction pipeline writes to `shared/project/` (singular) via
    promote.py. The old scaffold created `shared/projects/` (plural) which
    stayed empty forever and confused users. Don't create it."""
    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    assert not (vault / "shared" / "projects").exists(), (
        "shared/projects/ (plural) is the legacy empty dir; extraction writes "
        "to shared/project/ (singular), which is what scaffold should create"
    )


def test_home_template_describes_auto_populated_types(tmp_path: Path):
    """HOME.md must accurately describe the four extraction-populated Tier 2
    categories (feedback, user, reference, project), not only the manual ones."""
    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    home = (vault / "HOME.md").read_text(encoding="utf-8")
    for wikilink in ("shared/feedback", "shared/user", "shared/reference", "shared/project"):
        assert f"[[{wikilink}]]" in home, (
            f"HOME.md must link to [[{wikilink}]] — auto-populated Tier 2 dir"
        )
    # And the old broken plural pointer must be gone
    assert "[[shared/projects]]" not in home, (
        "HOME.md still points to the legacy empty shared/projects/ dir"
    )


def test_scaffold_idempotent(tmp_path: Path):
    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    (vault / "HOME.md").write_text("# user customized")
    scaffold.scaffold_vault(vault)
    assert (vault / "HOME.md").read_text() == "# user customized"


def test_scaffold_writes_config_with_vault_root(tmp_path: Path):
    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    cfg = json.loads((vault / "mnemo.config.json").read_text())
    assert cfg["vaultRoot"] == str(vault)
