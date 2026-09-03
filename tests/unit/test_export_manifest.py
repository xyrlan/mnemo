from __future__ import annotations

import json
from pathlib import Path


def test_write_and_read_round_trip(tmp_vault: Path):
    from mnemo.core.export import manifest as M

    M.write_manifest(
        tmp_vault, "app", host="claude", target="rules", cwd="/r/app",
        path=".claude/rules/mnemo.md", rules={"a": "h1", "b": "h2"},
    )
    p = tmp_vault / ".mnemo" / "export" / "app.json"
    assert p.exists()
    data = M.read_manifest(tmp_vault, "app")
    assert data["host"] == "claude" and data["rules"] == {"a": "h1", "b": "h2"}
    assert data["cwd"] == "/r/app" and "exported_at" in data
    assert M.read_manifest(tmp_vault, "other") is None


def test_corrupt_manifest_reads_as_none(tmp_vault: Path):
    from mnemo.core.export import manifest as M

    p = tmp_vault / ".mnemo" / "export" / "app.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    assert M.read_manifest(tmp_vault, "app") is None


def test_delete_manifest(tmp_vault: Path):
    from mnemo.core.export import manifest as M

    M.write_manifest(tmp_vault, "app", host="claude", target="rules", cwd="/r/app",
                     path=".claude/rules/mnemo.md", rules={})
    assert M.delete_manifest(tmp_vault, "app") is True
    assert M.delete_manifest(tmp_vault, "app") is False


def test_exported_slugs_only_for_claude_loaded_targets_in_that_repo(tmp_vault: Path):
    from mnemo.core.export import manifest as M

    M.write_manifest(tmp_vault, "app", host="claude", target="rules", cwd="/r/app",
                     path=".claude/rules/mnemo.md", rules={"a": "h", "b": "h"})
    assert M.exported_slugs_for(tmp_vault, "app", repo_root="/r/app") == {"a", "b"}
    assert M.exported_slugs_for(tmp_vault, "app", repo_root="/elsewhere/app") == set()
    assert M.exported_slugs_for(tmp_vault, "nope", repo_root="/r/app") == set()

    M.write_manifest(tmp_vault, "cur", host="cursor", target="rules", cwd="/r/cur",
                     path=".cursor/rules/mnemo.mdc", rules={"c": "h"})
    assert M.exported_slugs_for(tmp_vault, "cur", repo_root="/r/cur") == set()


def test_staleness_counts_changed_added_and_removed(tmp_vault: Path):
    from mnemo.core.export import manifest as M

    M.write_manifest(tmp_vault, "app", host="claude", target="rules", cwd="/r/app",
                     path=".claude/rules/mnemo.md", rules={"a": "h1", "b": "h2"})
    # a unchanged, b changed, c new, and nothing for a removed 'd'
    assert M.staleness(tmp_vault, "app", current={"a": "h1", "b": "X", "c": "h3"}) == (2, 2)
    assert M.staleness(tmp_vault, "app", current={"a": "h1", "b": "h2"}) == (2, 0)
    assert M.staleness(tmp_vault, "app", current={"a": "h1"}) == (2, 1)
    assert M.staleness(tmp_vault, "none", current={}) is None
