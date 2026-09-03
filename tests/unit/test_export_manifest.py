from __future__ import annotations

import json
from pathlib import Path

from mnemo.core.export.render import START_MARKER


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


def test_exported_slugs_only_for_claude_loaded_targets_in_that_repo(tmp_vault: Path, tmp_path: Path):
    from mnemo.core.export import manifest as M

    repo = tmp_path / "r" / "app"
    rules_file = repo / ".claude" / "rules" / "mnemo.md"
    rules_file.parent.mkdir(parents=True)
    rules_file.write_text(START_MARKER + " — generated -->\nbody\n", encoding="utf-8")

    M.write_manifest(tmp_vault, "app", host="claude", target="rules", cwd=str(repo),
                     path=".claude/rules/mnemo.md", rules={"a": "h", "b": "h"})
    # file present, with the marker, in the repo the manifest's path resolves under
    assert M.exported_slugs_for(tmp_vault, "app", repo_root=str(repo)) == {"a", "b"}
    # a different repo root that lacks the file falls back to the manifest's
    # recorded cwd, which still has it (see the dedicated fallback test for
    # the case where neither location has the file)
    other = tmp_path / "elsewhere" / "app"
    other.mkdir(parents=True)
    assert M.exported_slugs_for(tmp_vault, "app", repo_root=str(other)) == {"a", "b"}
    # unknown project: no manifest at all
    assert M.exported_slugs_for(tmp_vault, "nope", repo_root=str(repo)) == set()

    # file present but without the mnemo marker: not something Claude is loading
    no_marker_repo = tmp_path / "r" / "nomark"
    no_marker_file = no_marker_repo / ".claude" / "rules" / "mnemo.md"
    no_marker_file.parent.mkdir(parents=True)
    no_marker_file.write_text("not a mnemo block\n", encoding="utf-8")
    M.write_manifest(tmp_vault, "nomark", host="claude", target="rules", cwd=str(no_marker_repo),
                     path=".claude/rules/mnemo.md", rules={"a": "h"})
    assert M.exported_slugs_for(tmp_vault, "nomark", repo_root=str(no_marker_repo)) == set()

    # cursor host: never counts as "Claude already loading it"
    cur_repo = tmp_path / "r" / "cur"
    cur_file = cur_repo / ".cursor" / "rules" / "mnemo.mdc"
    cur_file.parent.mkdir(parents=True)
    cur_file.write_text(START_MARKER + " — generated -->\nbody\n", encoding="utf-8")
    M.write_manifest(tmp_vault, "cur", host="cursor", target="rules", cwd=str(cur_repo),
                     path=".cursor/rules/mnemo.mdc", rules={"c": "h"})
    assert M.exported_slugs_for(tmp_vault, "cur", repo_root=str(cur_repo)) == set()


def test_exported_slugs_falls_back_to_manifest_cwd_when_repo_root_lacks_the_file(
    tmp_vault: Path, tmp_path: Path
):
    """The hook probes the tree it is standing in; failing that, the tree the
    export was written from (a worktree checked out from a different path
    than the one the manifest's ``cwd`` recorded, or vice versa)."""
    from mnemo.core.export import manifest as M

    written_from = tmp_path / "written-from" / "app"
    rules_file = written_from / ".claude" / "rules" / "mnemo.md"
    rules_file.parent.mkdir(parents=True)
    rules_file.write_text(START_MARKER + " — generated -->\nbody\n", encoding="utf-8")

    M.write_manifest(tmp_vault, "app", host="claude", target="rules", cwd=str(written_from),
                     path=".claude/rules/mnemo.md", rules={"a": "h", "b": "h"})

    # A different repo_root that does NOT have the file: falls back to cwd.
    standing_in = tmp_path / "standing-in" / "app"
    standing_in.mkdir(parents=True)
    assert M.exported_slugs_for(tmp_vault, "app", repo_root=str(standing_in)) == {"a", "b"}

    # Neither repo_root nor cwd has the file: empty.
    M.write_manifest(tmp_vault, "gone", host="claude", target="rules", cwd=str(tmp_path / "nowhere"),
                     path=".claude/rules/mnemo.md", rules={"a": "h"})
    assert M.exported_slugs_for(tmp_vault, "gone", repo_root=str(tmp_path / "also-nowhere")) == set()


def test_staleness_counts_changed_added_and_removed(tmp_vault: Path):
    from mnemo.core.export import manifest as M

    M.write_manifest(tmp_vault, "app", host="claude", target="rules", cwd="/r/app",
                     path=".claude/rules/mnemo.md", rules={"a": "h1", "b": "h2"})
    # a unchanged, b changed, c new
    assert M.staleness(tmp_vault, "app", current={"a": "h1", "b": "X", "c": "h3"}) == (2, 2)
    assert M.staleness(tmp_vault, "app", current={"a": "h1", "b": "h2"}) == (2, 0)
    # b removed from current: counted as a change (exported value no longer matches)
    assert M.staleness(tmp_vault, "app", current={"a": "h1"}) == (2, 1)
    assert M.staleness(tmp_vault, "none", current={}) is None
