# tests/unit/test_wiki.py
from __future__ import annotations

import re
from pathlib import Path

import pytest

from mnemo.core import wiki


def test_promote_copies_to_wiki_sources(tmp_vault: Path):
    src = tmp_vault / "bots" / "myrepo" / "logs" / "2026-04-11.md"
    src.parent.mkdir(parents=True)
    src.write_text("# my notes\nbody")
    cfg = {"vaultRoot": str(tmp_vault)}
    out = wiki.promote_note(src, cfg)
    assert out.parent == tmp_vault / "wiki" / "sources"
    text = out.read_text()
    assert "---" in text
    assert "origin:" in text
    assert "promoted_at:" in text
    assert "# my notes" in text
    assert "body" in text


def test_promote_preserves_existing_frontmatter(tmp_vault: Path):
    src = tmp_vault / "shared" / "people" / "alice.md"
    src.parent.mkdir(parents=True)
    src.write_text("---\nname: Alice\n---\n# Alice")
    cfg = {"vaultRoot": str(tmp_vault)}
    out = wiki.promote_note(src, cfg)
    text = out.read_text()
    assert "name: Alice" in text
    assert "promoted_at:" in text


def test_promote_idempotent_overwrite(tmp_vault: Path):
    src = tmp_vault / "shared" / "x.md"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("v1")
    cfg = {"vaultRoot": str(tmp_vault)}
    wiki.promote_note(src, cfg)
    src.write_text("v2")
    out = wiki.promote_note(src, cfg)
    assert "v2" in out.read_text()


def test_compile_wiki_copies_sources_to_compiled(tmp_vault: Path):
    cfg = {"vaultRoot": str(tmp_vault)}
    src = tmp_vault / "wiki" / "sources" / "alpha.md"
    src.write_text("# Alpha")
    (tmp_vault / "wiki" / "sources" / "beta.md").write_text("# Beta")
    wiki.compile_wiki(cfg)
    compiled = tmp_vault / "wiki" / "compiled"
    assert (compiled / "alpha.md").read_text() == "# Alpha"
    assert (compiled / "beta.md").read_text() == "# Beta"


def test_compile_wiki_writes_index(tmp_vault: Path):
    cfg = {"vaultRoot": str(tmp_vault)}
    (tmp_vault / "wiki" / "sources" / "alpha.md").write_text("# Alpha")
    (tmp_vault / "wiki" / "sources" / "beta.md").write_text("# Beta")
    wiki.compile_wiki(cfg)
    index = (tmp_vault / "wiki" / "compiled" / "index.md").read_text()
    assert "alpha" in index
    assert "beta" in index
    assert "[[alpha]]" in index or "alpha.md" in index


def test_compile_wiki_idempotent(tmp_vault: Path):
    cfg = {"vaultRoot": str(tmp_vault)}
    (tmp_vault / "wiki" / "sources" / "a.md").write_text("v1")
    wiki.compile_wiki(cfg)
    wiki.compile_wiki(cfg)
    assert (tmp_vault / "wiki" / "compiled" / "a.md").read_text() == "v1"
