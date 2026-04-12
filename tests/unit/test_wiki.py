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
