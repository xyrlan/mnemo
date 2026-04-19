"""Vault-wide index with consumer-visibility gate + per-doc projects/universal."""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core.reflex.index import build_index

FRONTMATTER_TEMPLATE = (
    "---\n"
    "name: {name}\n"
    "description: {description}\n"
    "tags:\n"
    "{tags_block}"
    "sources:\n"
    "{sources_block}"
    "{extra}"
    "stability: {stability}\n"
    "---\n"
    "{body}\n"
)


def _write_rule(vault: Path, subdir: str, filename: str, **kw) -> Path:
    path = vault / "shared" / subdir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    tags = kw.get("tags") or ["auto-promoted"]
    sources = kw.get("sources") or ["bots/mnemo/memory/example.md"]
    aliases = kw.get("aliases")
    extra = ""
    if aliases:
        extra = "aliases:\n" + "".join(f"  - {a}\n" for a in aliases)
    path.write_text(FRONTMATTER_TEMPLATE.format(
        name=kw.get("name", filename.replace(".md", "")),
        description=kw.get("description", "desc"),
        tags_block="".join(f"  - {t}\n" for t in tags),
        sources_block="".join(f"  - {s}\n" for s in sources),
        extra=extra,
        stability=kw.get("stability", "stable"),
        body=kw.get("body", "Actual rule body content."),
    ), encoding="utf-8")
    return path


def test_build_index_includes_only_consumer_visible_rules(tmp_vault):
    # Visible rule
    _write_rule(tmp_vault, "feedback", "keep.md", name="keep")
    # Inbox rule — must be skipped
    _write_rule(tmp_vault, "_inbox/feedback", "draft.md", name="draft")
    # needs-review — must be skipped
    _write_rule(tmp_vault, "feedback", "review.md", name="review",
                tags=["needs-review"])
    # evolving — must be skipped
    _write_rule(tmp_vault, "feedback", "evolving.md", name="flaky",
                stability="evolving")

    idx = build_index(tmp_vault, universal_threshold=2)
    slugs = set(idx["docs"].keys())
    assert "keep" in slugs
    assert slugs == {"keep"}, f"expected only 'keep', got {slugs}"


def test_build_index_emits_projects_and_universal_per_doc(tmp_vault):
    _write_rule(tmp_vault, "feedback", "a.md", name="a",
                sources=["bots/projA/memory/x.md"])
    _write_rule(tmp_vault, "feedback", "b.md", name="b",
                sources=["bots/projA/memory/y.md", "bots/projB/memory/z.md"])

    idx = build_index(tmp_vault, universal_threshold=2)

    assert idx["docs"]["a"]["projects"] == ["projA"]
    assert idx["docs"]["a"]["universal"] is False
    assert idx["docs"]["b"]["projects"] == ["projA", "projB"]
    assert idx["docs"]["b"]["universal"] is True


def test_build_index_indexes_aliases_field(tmp_vault):
    _write_rule(tmp_vault, "feedback", "c.md", name="c",
                description="Mock database in tests",
                aliases=["banco", "database", "db"])

    idx = build_index(tmp_vault, universal_threshold=2)

    # "banco" must appear in postings → points back to slug "c".
    assert "banco" in idx["postings"]
    assert any(p["slug"] == "c" for p in idx["postings"]["banco"])
    assert idx["docs"]["c"]["field_length"]["aliases"] == 3


def test_build_index_schema_shape(tmp_vault):
    _write_rule(tmp_vault, "feedback", "a.md", name="a")
    idx = build_index(tmp_vault, universal_threshold=2)

    assert idx["schema_version"] == 1
    assert "generated_at" in idx
    # No scope / project top-level fields (C3 fix).
    assert "scope" not in idx
    assert "project" not in idx
    assert isinstance(idx["avg_field_length"], dict)
    assert set(idx["avg_field_length"]) == {
        "name", "topic_tags", "aliases", "description", "body",
    }
    assert isinstance(idx["postings"], dict)
    assert isinstance(idx["docs"], dict)
