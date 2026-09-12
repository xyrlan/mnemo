"""bootstrap_cases must recover pre-cutover pairs whose hit_slugs hold rule names.

``hit_slugs`` recorded rule *names* until 2026-09-08 and slugs after (#193).
The orphan filter resolves by slug, so every name-shaped pair was dropped and
the case set collapsed to the post-cutover tail. These tests pin the recovery:
a name that maps to exactly one live slug is rewritten, and a name that maps to
none — or to more than one — is still dropped rather than guessed.
"""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core.mcp.recall import bootstrap_cases
from mnemo.core.rule_activation.index import build_index, write_index


def _write_log(p: Path, entries: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def _seed_rule(vault: Path, slug: str, name: str, topic: str, project: str = "mnemo") -> None:
    """Seed a rule whose file stem (its slug) differs from its display name.

    That divergence is the whole point: the log stores the name, the index is
    keyed by the slug, and the mapping between them is what #193 recovers.
    """
    d = vault / "shared" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.md").write_text(
        f"---\nname: {json.dumps(name)}\nslug: {slug}\ndescription: 'd'\ntype: feedback\n"
        f"extracted_at: 2026-04-20T10:00:00\nstability: stable\n"
        f"sources:\n  - bots/{project}/x.md\ntags:\n  - {topic}\n---\nbody\n",
        encoding="utf-8",
    )


def _build_and_write_index(vault: Path) -> None:
    write_index(vault, build_index(vault, universal_threshold=2))


def _pair(ts_list: str, ts_read: str, topic: str, hit: str, project: str = "mnemo") -> list[dict]:
    return [
        {"timestamp": ts_list, "tool": "list_rules_by_topic",
         "args": {"topic": topic, "scope": "project"}, "project": project,
         "result_count": 1, "hit_slugs": [hit]},
        {"timestamp": ts_read, "tool": "read_mnemo_rule",
         "args": {"slug": hit}, "project": project},
    ]


def test_name_shaped_pair_is_recovered_to_its_slug(tmp_path):
    """A logged rule name resolving to exactly one live slug becomes a case."""
    _seed_rule(tmp_path, "the-real-slug", "A Human Readable Rule Name", topic="workflow")
    _build_and_write_index(tmp_path)
    log = tmp_path / ".mnemo" / "mcp-access-log.jsonl"
    _write_log(log, _pair(
        "2026-04-20T10:00:00Z", "2026-04-20T10:00:05Z",
        "workflow", "A Human Readable Rule Name",
    ))

    cases, dropped = bootstrap_cases(
        log, pair_window_s=120.0, vault_root=tmp_path, return_orphan_count=True,
    )

    assert dropped == 0
    assert [c["expect_slug"] for c in cases] == ["the-real-slug"]


def test_recovered_case_id_uses_the_resolved_slug(tmp_path):
    """The case id must key off the slug so ids stay stable across the cutover."""
    _seed_rule(tmp_path, "the-real-slug", "A Human Readable Rule Name", topic="workflow")
    _build_and_write_index(tmp_path)
    log = tmp_path / ".mnemo" / "mcp-access-log.jsonl"
    _write_log(log, _pair(
        "2026-04-20T10:00:00Z", "2026-04-20T10:00:05Z",
        "workflow", "A Human Readable Rule Name",
    ))

    cases = bootstrap_cases(log, pair_window_s=120.0, vault_root=tmp_path)

    assert cases[0]["id"] == "mnemo:workflow:the-real-slug"


def test_name_with_no_live_rule_is_still_dropped(tmp_path):
    """An unresolvable name is an orphan — never guessed into a case."""
    _seed_rule(tmp_path, "the-real-slug", "A Human Readable Rule Name", topic="workflow")
    _build_and_write_index(tmp_path)
    log = tmp_path / ".mnemo" / "mcp-access-log.jsonl"
    _write_log(log, _pair(
        "2026-04-20T10:00:00Z", "2026-04-20T10:00:05Z",
        "workflow", "A Rule That Was Deleted Long Ago",
    ))

    cases, dropped = bootstrap_cases(
        log, pair_window_s=120.0, vault_root=tmp_path, return_orphan_count=True,
    )

    assert cases == []
    assert dropped == 1


def test_name_matching_two_rules_is_dropped_as_ambiguous(tmp_path):
    """A name resolving to more than one slug is not recoverable — drop it."""
    _seed_rule(tmp_path, "slug-one", "Duplicated Name", topic="workflow")
    _seed_rule(tmp_path, "slug-two", "Duplicated Name", topic="workflow")
    _build_and_write_index(tmp_path)
    log = tmp_path / ".mnemo" / "mcp-access-log.jsonl"
    _write_log(log, _pair(
        "2026-04-20T10:00:00Z", "2026-04-20T10:00:05Z",
        "workflow", "Duplicated Name",
    ))

    cases, dropped = bootstrap_cases(
        log, pair_window_s=120.0, vault_root=tmp_path, return_orphan_count=True,
    )

    assert cases == []
    assert dropped == 1


def test_name_resolving_outside_the_topic_bucket_is_dropped(tmp_path):
    """Resolution is not enough: the rule must still be tagged with the topic.

    Otherwise the replayed retrieval could never return it and the case would
    score a guaranteed miss that says nothing about ranking.
    """
    _seed_rule(tmp_path, "the-real-slug", "A Human Readable Rule Name", topic="testing")
    _build_and_write_index(tmp_path)
    log = tmp_path / ".mnemo" / "mcp-access-log.jsonl"
    _write_log(log, _pair(
        "2026-04-20T10:00:00Z", "2026-04-20T10:00:05Z",
        "workflow", "A Human Readable Rule Name",
    ))

    cases, dropped = bootstrap_cases(
        log, pair_window_s=120.0, vault_root=tmp_path, return_orphan_count=True,
    )

    assert cases == []
    assert dropped == 1


def test_doubled_apostrophe_in_log_resolves_to_the_live_name(tmp_path):
    """Some logged names escaped ``'`` as ``''``; unescaping must still resolve.

    Real log values carry this (9 distinct names, 37 rows), so without the
    fallback those pairs look deleted when the rule is alive.
    """
    _seed_rule(
        tmp_path, "merchant-local-timezone",
        "Always calculate metrics in merchant's local timezone", topic="workflow",
    )
    _build_and_write_index(tmp_path)
    log = tmp_path / ".mnemo" / "mcp-access-log.jsonl"
    _write_log(log, _pair(
        "2026-04-20T10:00:00Z", "2026-04-20T10:00:05Z",
        "workflow", "Always calculate metrics in merchant''s local timezone",
    ))

    cases = bootstrap_cases(log, pair_window_s=120.0, vault_root=tmp_path)

    assert [c["expect_slug"] for c in cases] == ["merchant-local-timezone"]


def test_literal_doubled_apostrophe_name_is_not_shadowed(tmp_path):
    """A live name that really contains ``''`` (e.g. ``z.literal('')``) wins on exact match.

    The unescape is a fallback, not a rewrite: it must never steal a pair whose
    name matches a live rule verbatim.
    """
    _seed_rule(
        tmp_path, "zod-empty-literal",
        "Email validation: z.string().email().or(z.literal(''))", topic="workflow",
    )
    _build_and_write_index(tmp_path)
    log = tmp_path / ".mnemo" / "mcp-access-log.jsonl"
    _write_log(log, _pair(
        "2026-04-20T10:00:00Z", "2026-04-20T10:00:05Z",
        "workflow", "Email validation: z.string().email().or(z.literal(''))",
    ))

    cases = bootstrap_cases(log, pair_window_s=120.0, vault_root=tmp_path)

    assert [c["expect_slug"] for c in cases] == ["zod-empty-literal"]


def test_slug_shaped_pairs_are_unaffected(tmp_path):
    """Post-cutover slug-shaped entries keep resolving exactly as before."""
    _seed_rule(tmp_path, "the-real-slug", "A Human Readable Rule Name", topic="workflow")
    _build_and_write_index(tmp_path)
    log = tmp_path / ".mnemo" / "mcp-access-log.jsonl"
    _write_log(log, _pair(
        "2026-04-20T10:00:00Z", "2026-04-20T10:00:05Z",
        "workflow", "the-real-slug",
    ))

    cases, dropped = bootstrap_cases(
        log, pair_window_s=120.0, vault_root=tmp_path, return_orphan_count=True,
    )

    assert dropped == 0
    assert [c["expect_slug"] for c in cases] == ["the-real-slug"]


def test_name_and_slug_observations_of_one_rule_collapse_to_one_case(tmp_path):
    """The same rule logged either side of the cutover must not double-count.

    Dedup keys on expect_slug, so recovery has to happen before the dedup or
    the case set inflates with duplicates that share every measured property.
    """
    _seed_rule(tmp_path, "the-real-slug", "A Human Readable Rule Name", topic="workflow")
    _build_and_write_index(tmp_path)
    log = tmp_path / ".mnemo" / "mcp-access-log.jsonl"
    _write_log(
        log,
        _pair("2026-04-20T10:00:00Z", "2026-04-20T10:00:05Z",
              "workflow", "A Human Readable Rule Name")
        + _pair("2026-09-10T10:00:00Z", "2026-09-10T10:00:05Z",
                "workflow", "the-real-slug"),
    )

    cases = bootstrap_cases(log, pair_window_s=120.0, vault_root=tmp_path)

    assert len(cases) == 1
    assert cases[0]["expect_slug"] == "the-real-slug"


def test_no_vault_root_leaves_names_untouched(tmp_path):
    """Without a vault there is no mapping to resolve against — keep prior shape."""
    log = tmp_path / "log.jsonl"
    _write_log(log, _pair(
        "2026-04-20T10:00:00Z", "2026-04-20T10:00:05Z",
        "workflow", "A Human Readable Rule Name",
    ))

    cases = bootstrap_cases(log, pair_window_s=120.0)

    assert [c["expect_slug"] for c in cases] == ["A Human Readable Rule Name"]
