# tests/unit/test_source_paths.py
"""Source paths recorded in rules/state must be vault-relative, not absolute.

The scanner walks ``vault_root / "bots"`` with an absolute ``vault_root``, so
``str(mf.path)`` produced machine-absolute source paths for every rule written
from a scanned file, while LLM-extracted sources stayed vault-relative. The
v0.15.1 dogfood measured 155 of 337 live rules (46%) carrying absolute paths —
they break the moment the vault moves. ``vault_relative_source`` is the single
chokepoint that normalizes both.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.extract.source_paths import vault_relative_source


def test_absolute_path_under_vault_becomes_relative():
    vault = Path("/Users/x/mnemo")
    src = "/Users/x/mnemo/bots/meunu/briefings/sessions/abc.md"
    assert vault_relative_source(src, vault) == "bots/meunu/briefings/sessions/abc.md"


def test_pathlike_input_is_accepted():
    vault = Path("/Users/x/mnemo")
    src = Path("/Users/x/mnemo/bots/meunu/memory/foo.md")
    assert vault_relative_source(src, vault) == "bots/meunu/memory/foo.md"


def test_already_relative_path_is_unchanged():
    vault = Path("/Users/x/mnemo")
    assert vault_relative_source("bots/meunu/memory/foo.md", vault) == "bots/meunu/memory/foo.md"


def test_absolute_path_outside_vault_is_left_alone():
    vault = Path("/Users/x/mnemo")
    src = "/etc/passwd"
    assert vault_relative_source(src, vault) == "/etc/passwd"


def test_relocated_by_bots_segment_when_prefix_differs():
    """A source written under an old vault location still relativizes."""
    vault = Path("/Users/x/mnemo")
    src = "/old/home/vault/bots/clubinho/memory/bar.md"
    assert vault_relative_source(src, vault) == "bots/clubinho/memory/bar.md"


def test_posix_separators_on_all_platforms():
    vault = Path("/Users/x/mnemo")
    src = "/Users/x/mnemo/bots/a/memory/b.md"
    out = vault_relative_source(src, vault)
    assert "\\" not in out
