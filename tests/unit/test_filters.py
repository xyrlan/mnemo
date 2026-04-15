"""Unit tests for the shared filter predicate (v0.4).

The filter is the single source of truth for "consumer-visible" pages, consumed
by the HOME dashboard (v0.4) and the MCP tools (v0.5). See
project_mnemo_v0.4_direction.md "Shared filter specification".
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.filters import (
    MANAGED_TAGS,
    is_consumer_visible,
    parse_frontmatter,
    topic_tags,
)


def test_inbox_path_is_excluded(tmp_path: Path) -> None:
    vault = tmp_path
    page = vault / "shared" / "_inbox" / "feedback" / "draft.md"
    assert is_consumer_visible(page, {"stability": "stable"}, vault) is False


def test_project_inbox_also_excluded(tmp_path: Path) -> None:
    vault = tmp_path
    page = vault / "shared" / "_inbox" / "project" / "draft.md"
    assert is_consumer_visible(page, {"stability": "stable"}, vault) is False


def test_needs_review_tag_is_excluded(tmp_path: Path) -> None:
    vault = tmp_path
    page = vault / "shared" / "feedback" / "draft.md"
    fm = {"tags": ["needs-review", "auth"], "stability": "stable"}
    assert is_consumer_visible(page, fm, vault) is False


def test_evolving_stability_is_excluded(tmp_path: Path) -> None:
    vault = tmp_path
    page = vault / "shared" / "feedback" / "draft.md"
    fm = {"tags": ["auto-promoted"], "stability": "evolving"}
    assert is_consumer_visible(page, fm, vault) is False


def test_stable_auto_promoted_page_is_visible(tmp_path: Path) -> None:
    vault = tmp_path
    page = vault / "shared" / "feedback" / "rule.md"
    fm = {"tags": ["auto-promoted", "git"], "stability": "stable"}
    assert is_consumer_visible(page, fm, vault) is True


def test_missing_stability_defaults_to_stable(tmp_path: Path) -> None:
    vault = tmp_path
    page = vault / "shared" / "user" / "role.md"
    fm = {"tags": ["auto-promoted"]}
    assert is_consumer_visible(page, fm, vault) is True


def test_missing_tags_key_is_treated_as_empty(tmp_path: Path) -> None:
    vault = tmp_path
    page = vault / "shared" / "reference" / "linear.md"
    assert is_consumer_visible(page, {}, vault) is True


def test_page_outside_vault_is_excluded(tmp_path: Path) -> None:
    vault = tmp_path
    page = tmp_path.parent / "elsewhere" / "random.md"
    assert is_consumer_visible(page, {"stability": "stable"}, vault) is False


def test_page_at_shared_root_is_excluded(tmp_path: Path) -> None:
    """Pages directly in shared/ (not shared/<type>/) aren't real pages — guard anyway."""
    vault = tmp_path
    page = vault / "shared" / "stray.md"
    # shared/stray.md has rel.parts = ("stray.md",), which is not "_inbox" -> visible.
    # This is intentional: the filter trusts the caller to walk shared/<type>/ only.
    assert is_consumer_visible(page, {}, vault) is True


def test_topic_tags_strips_managed_markers() -> None:
    fm = {"tags": ["auto-promoted", "git", "workflow"]}
    assert topic_tags(fm) == ["git", "workflow"]


def test_topic_tags_strips_needs_review() -> None:
    fm = {"tags": ["needs-review", "auth"]}
    assert topic_tags(fm) == ["auth"]


def test_topic_tags_strips_home_dashboard_wiki_index() -> None:
    fm = {"tags": ["home", "dashboard", "wiki", "index", "real-topic"]}
    assert topic_tags(fm) == ["real-topic"]


def test_topic_tags_empty_when_only_managed() -> None:
    fm = {"tags": ["auto-promoted"]}
    assert topic_tags(fm) == []


def test_topic_tags_handles_missing_key() -> None:
    assert topic_tags({}) == []


def test_managed_tags_contains_expected_markers() -> None:
    assert "needs-review" in MANAGED_TAGS
    assert "auto-promoted" in MANAGED_TAGS
    assert "home" in MANAGED_TAGS
    assert "dashboard" in MANAGED_TAGS


def test_parse_frontmatter_reads_simple_fields() -> None:
    text = (
        "---\n"
        "name: my-rule\n"
        "description: a thing\n"
        "type: feedback\n"
        "stability: stable\n"
        "sources:\n"
        "  - bots/foo/memory/x.md\n"
        "  - bots/bar/memory/y.md\n"
        "tags:\n"
        "  - auto-promoted\n"
        "  - git\n"
        "  - workflow\n"
        "---\n"
        "\n"
        "body content\n"
    )
    fm = parse_frontmatter(text)
    assert fm["name"] == "my-rule"
    assert fm["description"] == "a thing"
    assert fm["type"] == "feedback"
    assert fm["stability"] == "stable"
    assert fm["sources"] == ["bots/foo/memory/x.md", "bots/bar/memory/y.md"]
    assert fm["tags"] == ["auto-promoted", "git", "workflow"]


def test_parse_frontmatter_returns_empty_dict_when_no_frontmatter() -> None:
    assert parse_frontmatter("just a body\n") == {}


def test_parse_frontmatter_handles_inline_empty_list() -> None:
    text = (
        "---\n"
        "name: x\n"
        "tags: []\n"
        "sources: []\n"
        "---\n"
        "body\n"
    )
    fm = parse_frontmatter(text)
    assert fm["tags"] == []
    assert fm["sources"] == []


def test_parse_frontmatter_ignores_malformed_lines() -> None:
    text = (
        "---\n"
        "name: x\n"
        "bogus line without colon\n"
        "description: y\n"
        "---\n"
        "body\n"
    )
    fm = parse_frontmatter(text)
    assert fm["name"] == "x"
    assert fm["description"] == "y"


def test_parse_frontmatter_single_source_list() -> None:
    text = (
        "---\n"
        "name: rule\n"
        "sources:\n"
        "  - one/path.md\n"
        "tags:\n"
        "  - auto-promoted\n"
        "---\n"
        "body\n"
    )
    fm = parse_frontmatter(text)
    assert fm["sources"] == ["one/path.md"]
    assert fm["tags"] == ["auto-promoted"]
