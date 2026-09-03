from __future__ import annotations

from pathlib import Path

import pytest

BLOCK = "<!-- mnemo:start — x -->\n## Rules\n\n### A  `a`\nbody\n<!-- mnemo:end -->\n"
BLOCK2 = "<!-- mnemo:start — y -->\n## Rules\n\n### B  `b`\nbody2\n<!-- mnemo:end -->\n"


def test_target_for_each_host(tmp_path: Path):
    from mnemo.core.export.writers import target_for

    assert target_for("claude", "auto", tmp_path).path == tmp_path / ".claude" / "rules" / "mnemo.md"
    assert target_for("claude", "auto", tmp_path).kind == "whole"
    t = target_for("claude", "claude-md", tmp_path)
    assert t.path == tmp_path / "CLAUDE.md" and t.kind == "block"
    c = target_for("cursor", "auto", tmp_path)
    assert c.path == tmp_path / ".cursor" / "rules" / "mnemo.mdc" and c.kind == "whole"
    assert c.prelude == "---\ndescription: Rules mnemo learned from you\nalwaysApply: true\n---\n"
    x = target_for("codex", "auto", tmp_path)
    assert x.path == tmp_path / "AGENTS.md" and x.kind == "block"


@pytest.mark.parametrize("host,target", [("cursor", "claude-md"), ("claude", "agents-md"), ("codex", "rules")])
def test_target_for_rejects_mismatched_pairs(tmp_path: Path, host, target):
    from mnemo.core.export.writers import TargetError, target_for

    with pytest.raises(TargetError):
        target_for(host, target, tmp_path)


def test_replace_block_appends_when_no_markers():
    from mnemo.core.export.writers import replace_block

    assert replace_block("# My project\n\nnotes\n", BLOCK) == "# My project\n\nnotes\n\n" + BLOCK
    assert replace_block("", BLOCK) == BLOCK


def test_replace_block_swaps_between_markers_and_keeps_the_rest():
    from mnemo.core.export.writers import replace_block

    text = "before\n\n" + BLOCK + "\nafter\n"
    assert replace_block(text, BLOCK2) == "before\n\n" + BLOCK2 + "\nafter\n"


def test_replace_block_refuses_a_single_marker():
    from mnemo.core.export.writers import MarkerError, replace_block

    with pytest.raises(MarkerError):
        replace_block("x\n<!-- mnemo:start — x -->\nno end\n", BLOCK)
    with pytest.raises(MarkerError):
        replace_block("x\n<!-- mnemo:end -->\n", BLOCK)


def test_strip_block_removes_it_or_returns_none():
    from mnemo.core.export.writers import strip_block

    assert strip_block("before\n\n" + BLOCK + "\nafter\n") == "before\n\nafter\n"
    assert strip_block("no block here\n") is None


def test_write_whole_target_writes_prelude_plus_block(tmp_path: Path):
    from mnemo.core.export.writers import target_for, write_target

    t = target_for("cursor", "auto", tmp_path)
    write_target(t, BLOCK)
    assert t.path.read_text(encoding="utf-8") == t.prelude + BLOCK


def test_write_block_target_round_trips_and_remove(tmp_path: Path):
    from mnemo.core.export.writers import remove_target, target_for, write_target

    t = target_for("codex", "auto", tmp_path)
    t.path.write_text("# Agents\n", encoding="utf-8")
    write_target(t, BLOCK)
    write_target(t, BLOCK2)
    assert t.path.read_text(encoding="utf-8") == "# Agents\n\n" + BLOCK2
    assert remove_target(t) is True
    assert t.path.read_text(encoding="utf-8") == "# Agents\n"
    assert remove_target(t) is False           # nothing left to strip


def test_remove_whole_target_deletes_file(tmp_path: Path):
    from mnemo.core.export.writers import remove_target, target_for, write_target

    t = target_for("claude", "auto", tmp_path)
    write_target(t, BLOCK)
    assert remove_target(t) is True and not t.path.exists()
    assert remove_target(t) is False
