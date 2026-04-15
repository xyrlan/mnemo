"""Unit tests for build_index, write_index, load_index, and projects_for_rule."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mnemo.core.rule_activation import (
    INDEX_VERSION,
    build_index,
    load_index,
    projects_for_rule,
    write_index,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_rule(
    vault: Path,
    filename: str,
    *,
    name: str = "test-rule",
    stability: str = "stable",
    tags: list[str] | None = None,
    sources: list[str] | None = None,
    enforce: str = "",
    activates_on: str = "",
    body: str = "**Why:** This is why.\n\n**How to apply:** Do this.",
    subdir: str = "feedback",
) -> Path:
    """Write a synthetic rule file into <vault>/shared/<subdir>/."""
    if tags is None:
        tags = ["auto-promoted"]
    if sources is None:
        sources = [f"bots/mnemo/memory/{filename}"]

    tag_lines = "\n".join(f"  - {t}" for t in tags)
    source_lines = "\n".join(f"  - {s}" for s in sources)

    parts = [
        "---",
        f"name: {name}",
        f"stability: {stability}",
        "tags:",
        tag_lines,
        "sources:",
        source_lines,
    ]
    if enforce:
        parts.append(enforce)
    if activates_on:
        parts.append(activates_on)
    parts += ["---", "", body]

    content = "\n".join(parts) + "\n"

    target_dir = vault / "shared" / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / filename
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# projects_for_rule
# ---------------------------------------------------------------------------


def test_projects_for_rule_derives_from_bots_prefix():
    """Single source file under bots/mnemo/ → ['mnemo']."""
    result = projects_for_rule(["bots/mnemo/memory/feedback_foo.md"])
    assert result == ["mnemo"]


def test_projects_for_rule_multi_source_multi_project():
    """Sources from bots/a and bots/b → ['a', 'b'] sorted."""
    result = projects_for_rule([
        "bots/sg-imports/memory/rule.md",
        "bots/agent-a/briefings/foo.md",
    ])
    assert result == ["agent-a", "sg-imports"]


def test_projects_for_rule_ignores_non_bots_paths():
    """Paths not under bots/<name>/ contribute nothing."""
    result = projects_for_rule([
        "shared/feedback/foo.md",
        "docs/guide.md",
        "bots/mnemo/memory/bar.md",
    ])
    assert result == ["mnemo"]


def test_projects_for_rule_empty_sources():
    """Empty list → empty list."""
    assert projects_for_rule([]) == []


def test_projects_for_rule_deduplicates():
    """Multiple sources from same project → single entry."""
    result = projects_for_rule([
        "bots/mnemo/memory/a.md",
        "bots/mnemo/memory/b.md",
    ])
    assert result == ["mnemo"]


# ---------------------------------------------------------------------------
# build_index — gating (NON-NEGOTIABLE)
# ---------------------------------------------------------------------------


def test_build_index_skips_needs_review_tagged_rules(tmp_vault: Path):
    """NON-NEGOTIABLE: rules tagged needs-review MUST NOT appear in index."""
    _write_rule(
        tmp_vault,
        "bad-rule.md",
        name="bad-rule",
        tags=["needs-review", "git"],
        enforce="enforce:\n  tool: Bash\n  deny_pattern: some.*pattern\n  reason: bad",
    )
    index = build_index(tmp_vault)
    assert "mnemo" not in index["enforce_by_project"]
    # Also verify it's not in malformed (it was silently skipped, not errored)
    assert not any("bad-rule.md" in e.get("path", "") for e in index["malformed"])


def test_build_index_skips_evolving_stability(tmp_vault: Path):
    """Rules with stability: evolving are skipped silently."""
    _write_rule(
        tmp_vault,
        "evolving-rule.md",
        name="evolving-rule",
        stability="evolving",
        enforce="enforce:\n  tool: Bash\n  deny_pattern: git push\n  reason: evolving",
    )
    index = build_index(tmp_vault)
    assert not index["enforce_by_project"]
    assert not any("evolving-rule.md" in e.get("path", "") for e in index["malformed"])


def test_build_index_skips_inbox_rules(tmp_vault: Path):
    """Rules in shared/_inbox/ are skipped (not consumer-visible)."""
    inbox_dir = tmp_vault / "shared" / "_inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    # Write directly (bypassing helper which writes to feedback/)
    content = (
        "---\n"
        "name: inbox-rule\n"
        "stability: stable\n"
        "tags:\n  - auto-promoted\n"
        "sources:\n  - bots/mnemo/memory/inbox-rule.md\n"
        "enforce:\n  tool: Bash\n  deny_pattern: git push\n  reason: inbox\n"
        "---\n\nbody\n"
    )
    (inbox_dir / "inbox-rule.md").write_text(content)
    # build_index only walks shared/feedback/, so this file won't even be seen.
    # But if it were walked, is_consumer_visible would reject it.
    index = build_index(tmp_vault)
    assert not index["enforce_by_project"]


def test_build_index_reuses_is_consumer_visible(tmp_vault: Path):
    """Mock is_consumer_visible to return False; rule must be excluded."""
    _write_rule(
        tmp_vault,
        "mocked-rule.md",
        name="mocked-rule",
        enforce="enforce:\n  tool: Bash\n  deny_pattern: git commit\n  reason: mocked",
    )
    with patch("mnemo.core.rule_activation.is_consumer_visible", return_value=False):
        index = build_index(tmp_vault)
    assert not index["enforce_by_project"]
    assert not any("mocked-rule.md" in e.get("path", "") for e in index["malformed"])


# ---------------------------------------------------------------------------
# build_index — malformed tracking
# ---------------------------------------------------------------------------


def test_build_index_records_malformed_block(tmp_vault: Path):
    """A rule with an uncompilable regex pattern appears in malformed."""
    _write_rule(
        tmp_vault,
        "bad-pattern-rule.md",
        name="bad-pattern-rule",
        # Use multi-line enforce block with invalid regex
        enforce=(
            "enforce:\n"
            "  tool: Bash\n"
            "  deny_pattern: '[unclosed'\n"
            "  reason: bad rule"
        ),
    )
    index = build_index(tmp_vault)
    malformed_paths = [e["path"] for e in index["malformed"]]
    assert any("bad-pattern-rule.md" in p for p in malformed_paths)
    # Error string should be descriptive
    errors = [e["error"] for e in index["malformed"]]
    assert any("deny_pattern" in err or "compile" in err for err in errors)


def test_build_index_records_malformed_activates_on(tmp_vault: Path):
    """A rule with unknown tool in activates_on appears in malformed."""
    _write_rule(
        tmp_vault,
        "bad-tool-rule.md",
        name="bad-tool-rule",
        activates_on=(
            "activates_on:\n"
            "  tools: [Bash]\n"  # Bash is not a valid enrich tool
            "  path_globs: ['**/*.py']"
        ),
    )
    index = build_index(tmp_vault)
    malformed_paths = [e["path"] for e in index["malformed"]]
    assert any("bad-tool-rule.md" in p for p in malformed_paths)


# ---------------------------------------------------------------------------
# build_index — happy path
# ---------------------------------------------------------------------------


def test_build_index_body_preview_truncated(tmp_vault: Path):
    """Rule body > 300 chars → preview is ~300 chars, truncated on whitespace."""
    long_body = "word " * 120  # 600 chars
    _write_rule(
        tmp_vault,
        "long-body-rule.md",
        name="long-body-rule",
        body=long_body,
        activates_on=(
            "activates_on:\n"
            "  tools: [Edit]\n"
            "  path_globs: ['**/*.py']"
        ),
    )
    index = build_index(tmp_vault)
    assert "mnemo" in index["enrich_by_project"]
    rules = index["enrich_by_project"]["mnemo"]
    assert len(rules) == 1
    preview = rules[0]["rule_body_preview"]
    assert len(preview) <= 300
    assert len(preview) > 0


def test_build_index_filters_system_tags_from_topic_tags(tmp_vault: Path):
    """tags [auto-promoted, react, needs-review, ui] → topic_tags is [react, ui]."""
    # needs-review would make is_consumer_visible return False, so use auto-promoted + custom tags
    _write_rule(
        tmp_vault,
        "tagged-rule.md",
        name="tagged-rule",
        tags=["auto-promoted", "react", "ui"],
        activates_on=(
            "activates_on:\n"
            "  tools: [Edit]\n"
            "  path_globs: ['**/*.tsx']"
        ),
    )
    index = build_index(tmp_vault)
    assert "mnemo" in index["enrich_by_project"]
    rule = index["enrich_by_project"]["mnemo"][0]
    # auto-promoted should be stripped
    assert "auto-promoted" not in rule["topic_tags"]
    assert "react" in rule["topic_tags"]
    assert "ui" in rule["topic_tags"]


def test_build_index_atomic_write_roundtrip(tmp_vault: Path):
    """build_index → write_index → load_index produces equal data."""
    _write_rule(
        tmp_vault,
        "roundtrip-rule.md",
        name="roundtrip-rule",
        enforce=(
            "enforce:\n"
            "  tool: Bash\n"
            "  deny_pattern: git push --force\n"
            "  reason: No force pushes"
        ),
    )
    original = build_index(tmp_vault)
    write_index(tmp_vault, original)
    loaded = load_index(tmp_vault)

    assert loaded is not None
    assert loaded["schema_version"] == INDEX_VERSION
    assert loaded["enforce_by_project"].keys() == original["enforce_by_project"].keys()
    assert loaded["malformed"] == original["malformed"]


def test_build_index_empty_vault(tmp_vault: Path):
    """Vault with no feedback rules produces an empty but valid index."""
    index = build_index(tmp_vault)
    assert index["schema_version"] == INDEX_VERSION
    assert index["enforce_by_project"] == {}
    assert index["enrich_by_project"] == {}
    assert index["malformed"] == []


def test_build_index_both_enforce_and_enrich(tmp_vault: Path):
    """Rule with both enforce and activates_on blocks is indexed in both buckets."""
    _write_rule(
        tmp_vault,
        "dual-rule.md",
        name="dual-rule",
        enforce=(
            "enforce:\n"
            "  tool: Bash\n"
            "  deny_pattern: rm -rf\n"
            "  reason: No deletes"
        ),
        activates_on=(
            "activates_on:\n"
            "  tools: [Edit, Write]\n"
            "  path_globs: ['**/*.py']"
        ),
    )
    index = build_index(tmp_vault)
    assert "mnemo" in index["enforce_by_project"]
    assert "mnemo" in index["enrich_by_project"]


# ---------------------------------------------------------------------------
# load_index — error handling
# ---------------------------------------------------------------------------


def test_load_index_returns_none_on_corrupt_json(tmp_vault: Path):
    """Corrupt JSON in index file → None."""
    mnemo_dir = tmp_vault / ".mnemo"
    mnemo_dir.mkdir(parents=True, exist_ok=True)
    (mnemo_dir / "rule-activation-index.json").write_text(
        "{ this is not valid JSON }", encoding="utf-8"
    )
    assert load_index(tmp_vault) is None


def test_load_index_returns_none_on_version_mismatch(tmp_vault: Path):
    """Index with schema_version != INDEX_VERSION → None."""
    mnemo_dir = tmp_vault / ".mnemo"
    mnemo_dir.mkdir(parents=True, exist_ok=True)
    bad_index = {
        "schema_version": 99,
        "built_at": "2026-01-01T00:00:00",
        "vault_root": str(tmp_vault),
        "enforce_by_project": {},
        "enrich_by_project": {},
        "malformed": [],
    }
    (mnemo_dir / "rule-activation-index.json").write_text(
        json.dumps(bad_index), encoding="utf-8"
    )
    assert load_index(tmp_vault) is None


def test_load_index_returns_none_on_missing_file(tmp_vault: Path):
    """Vault exists but no index file → None."""
    assert load_index(tmp_vault) is None


def test_load_index_returns_none_on_non_dict(tmp_vault: Path):
    """If JSON root is not a dict → None."""
    mnemo_dir = tmp_vault / ".mnemo"
    mnemo_dir.mkdir(parents=True, exist_ok=True)
    (mnemo_dir / "rule-activation-index.json").write_text("[1, 2, 3]")
    assert load_index(tmp_vault) is None


def test_load_index_valid(tmp_vault: Path):
    """Valid index with correct schema_version → returns dict."""
    index = build_index(tmp_vault)
    write_index(tmp_vault, index)
    result = load_index(tmp_vault)
    assert result is not None
    assert result["schema_version"] == INDEX_VERSION
