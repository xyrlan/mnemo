"""bootstrap_cases must drop pairs whose expect_slug no longer exists in the vault."""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core.mcp.recall import bootstrap_cases
from mnemo.core.rule_activation.index import build_index, write_index


def _write_log(p: Path, entries: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def _seed_rule(vault: Path, name: str, topic: str, project: str = "mnemo") -> None:
    d = vault / "shared" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    # filesystem-slug can differ from name — name is what the topic index returns.
    stem = name.lower().replace(" ", "-").replace("'", "").replace(",", "")
    (d / f"{stem}.md").write_text(
        f"---\nname: {name!r}\ndescription: 'd'\ntype: feedback\n"
        f"extracted_at: 2026-04-20T10:00:00\nstability: stable\n"
        f"sources:\n  - bots/{project}/x.md\ntags:\n  - {topic}\n---\nbody\n",
        encoding="utf-8",
    )


def _build_and_write_index(vault: Path) -> None:
    idx = build_index(vault, universal_threshold=2)
    write_index(vault, idx)


def test_orphan_case_is_dropped(tmp_path):
    log = tmp_path / ".mnemo" / "mcp-access-log.jsonl"
    _seed_rule(tmp_path, "Existing Rule", topic="workflow")
    _build_and_write_index(tmp_path)
    # Two pairs: one valid, one referencing a rule that isn't in the vault.
    _write_log(log, [
        {"timestamp": "2026-04-20T10:00:00Z", "tool": "list_rules_by_topic",
         "args": {"topic": "workflow", "scope": "project"}, "project": "mnemo",
         "result_count": 1, "hit_slugs": ["Existing Rule"]},
        {"timestamp": "2026-04-20T10:00:05Z", "tool": "read_mnemo_rule",
         "args": {"slug": "Existing Rule"}, "project": "mnemo"},
        {"timestamp": "2026-04-20T10:01:00Z", "tool": "list_rules_by_topic",
         "args": {"topic": "workflow", "scope": "project"}, "project": "mnemo",
         "result_count": 1, "hit_slugs": ["Ghost Rule That No Longer Exists"]},
        {"timestamp": "2026-04-20T10:01:05Z", "tool": "read_mnemo_rule",
         "args": {"slug": "Ghost Rule That No Longer Exists"}, "project": "mnemo"},
    ])

    cases = bootstrap_cases(log, pair_window_s=120.0, vault_root=tmp_path)
    expect_slugs = [c["expect_slug"] for c in cases]
    assert "Existing Rule" in expect_slugs
    assert "Ghost Rule That No Longer Exists" not in expect_slugs


def test_backward_compat_without_vault_root_keeps_all_cases(tmp_path):
    """Existing callers that don't pass vault_root must continue to get every pair."""
    log = tmp_path / "log.jsonl"
    _write_log(log, [
        {"timestamp": "2026-04-20T10:00:00Z", "tool": "list_rules_by_topic",
         "args": {"topic": "workflow"}, "project": "mnemo",
         "result_count": 1, "hit_slugs": ["Any Name"]},
        {"timestamp": "2026-04-20T10:00:05Z", "tool": "read_mnemo_rule",
         "args": {"slug": "Any Name"}, "project": "mnemo"},
    ])
    cases = bootstrap_cases(log, pair_window_s=120.0)
    assert len(cases) == 1
