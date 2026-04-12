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
    assert (vault / "shared" / "people").is_dir()
    assert (vault / "shared" / "companies").is_dir()
    assert (vault / "shared" / "projects").is_dir()
    assert (vault / "shared" / "decisions").is_dir()
    assert (vault / "wiki" / "sources").is_dir()
    assert (vault / "wiki" / "compiled").is_dir()


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
