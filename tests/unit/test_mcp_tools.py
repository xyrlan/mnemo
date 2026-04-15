"""Tests for mnemo.core.mcp.tools — pure read functions exposed via MCP."""
from __future__ import annotations

from pathlib import Path

from mnemo.core.mcp.tools import (
    get_mnemo_topics,
    list_rules_by_topic,
    read_mnemo_rule,
)


def _write_page(
    vault: Path,
    page_type: str,
    slug: str,
    *,
    tags: list[str],
    sources: list[str],
    stability: str = "stable",
    body: str = "the rule body\n",
    inbox: bool = False,
) -> Path:
    """Render a v0.4-shape page into the vault and return its path."""
    if inbox:
        target_dir = vault / "shared" / "_inbox" / page_type
    else:
        target_dir = vault / "shared" / page_type
    target_dir.mkdir(parents=True, exist_ok=True)
    sources_yaml = "\n".join(f"  - {s}" for s in sources)
    tags_yaml = "\n".join(f"  - {t}" for t in tags)
    text = (
        "---\n"
        f"name: {slug.replace('-', ' ').title()}\n"
        f"description: a description for {slug}\n"
        f"type: {page_type}\n"
        f"stability: {stability}\n"
        "sources:\n"
        f"{sources_yaml}\n"
        "tags:\n"
        f"{tags_yaml}\n"
        "---\n\n"
        f"{body}"
    )
    target = target_dir / f"{slug}.md"
    target.write_text(text)
    return target


# --- list_rules_by_topic ---


def test_list_rules_by_topic_returns_matching_slug(tmp_vault):
    _write_page(
        tmp_vault, "feedback", "use-yarn",
        tags=["auto-promoted", "package-management"],
        sources=["bots/agent-a/memory/m.md"],
    )
    _write_page(
        tmp_vault, "feedback", "no-commits",
        tags=["auto-promoted", "git"],
        sources=["bots/agent-a/memory/n.md", "bots/agent-b/memory/n.md"],
    )

    result = list_rules_by_topic(tmp_vault, "git")
    assert result == [
        {"slug": "no-commits", "type": "feedback", "source_count": 2}
    ]


def test_list_rules_by_topic_unknown_topic_returns_empty(tmp_vault):
    _write_page(
        tmp_vault, "feedback", "x",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],
    )
    assert list_rules_by_topic(tmp_vault, "nope") == []


def test_list_rules_by_topic_filters_inbox_drafts(tmp_vault):
    _write_page(
        tmp_vault, "feedback", "draft-rule",
        tags=["needs-review", "git"],
        sources=["bots/a/m.md", "bots/b/m.md"],
        inbox=True,
    )
    assert list_rules_by_topic(tmp_vault, "git") == []


def test_list_rules_by_topic_filters_needs_review_tag(tmp_vault):
    # Page lives in shared/<type>/ but is tagged needs-review (manual override).
    _write_page(
        tmp_vault, "feedback", "marked-draft",
        tags=["needs-review", "git"],
        sources=["bots/a/m.md"],
    )
    assert list_rules_by_topic(tmp_vault, "git") == []


def test_list_rules_by_topic_filters_evolving_stability(tmp_vault):
    _write_page(
        tmp_vault, "feedback", "in-flux",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],
        stability="evolving",
    )
    assert list_rules_by_topic(tmp_vault, "git") == []


def test_list_rules_by_topic_sorts_multi_source_first(tmp_vault):
    _write_page(
        tmp_vault, "feedback", "single-src",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],
    )
    _write_page(
        tmp_vault, "feedback", "triple-src",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md", "bots/b/m.md", "bots/c/m.md"],
    )
    _write_page(
        tmp_vault, "feedback", "double-src",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md", "bots/b/m.md"],
    )

    result = list_rules_by_topic(tmp_vault, "git")
    slugs = [r["slug"] for r in result]
    assert slugs == ["triple-src", "double-src", "single-src"]


def test_list_rules_by_topic_excludes_project_pages(tmp_vault):
    """Decision #2: project pages are never returned, even if a tag matches."""
    _write_page(
        tmp_vault, "project", "my-project",
        tags=["auto-promoted", "architecture"],
        sources=["bots/a/memory/project_my.md"],
    )
    assert list_rules_by_topic(tmp_vault, "architecture") == []


def test_list_rules_by_topic_unions_across_eligible_types(tmp_vault):
    _write_page(
        tmp_vault, "feedback", "f-rule",
        tags=["auto-promoted", "shared-topic"],
        sources=["bots/a/m.md"],
    )
    _write_page(
        tmp_vault, "user", "u-rule",
        tags=["auto-promoted", "shared-topic"],
        sources=["bots/a/m.md", "bots/b/m.md"],
    )
    _write_page(
        tmp_vault, "reference", "r-rule",
        tags=["auto-promoted", "shared-topic"],
        sources=["bots/a/m.md"],
    )

    result = list_rules_by_topic(tmp_vault, "shared-topic")
    types = sorted(r["type"] for r in result)
    assert types == ["feedback", "reference", "user"]


def test_list_rules_by_topic_handles_missing_shared_dir(tmp_vault):
    # No shared/feedback/ directory exists at all — must not raise.
    assert list_rules_by_topic(tmp_vault, "anything") == []


# --- read_mnemo_rule ---


def test_read_mnemo_rule_returns_full_page(tmp_vault):
    _write_page(
        tmp_vault, "feedback", "use-yarn",
        tags=["auto-promoted", "package-management"],
        sources=["bots/a/m.md"],
        body="Always use yarn.\n",
    )

    result = read_mnemo_rule(tmp_vault, "use-yarn")
    assert result is not None
    assert result["slug"] == "use-yarn"
    assert result["type"] == "feedback"
    assert result["body"] == "Always use yarn.\n"
    assert "package-management" in result["tags"]
    assert "auto-promoted" not in result["tags"]  # managed marker stripped
    assert result["sources"] == ["bots/a/m.md"]


def test_read_mnemo_rule_returns_none_for_unknown_slug(tmp_vault):
    assert read_mnemo_rule(tmp_vault, "ghost") is None


def test_read_mnemo_rule_returns_none_for_evolving_page(tmp_vault):
    _write_page(
        tmp_vault, "feedback", "in-flux",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],
        stability="evolving",
    )
    assert read_mnemo_rule(tmp_vault, "in-flux") is None


def test_read_mnemo_rule_returns_none_for_needs_review_page(tmp_vault):
    _write_page(
        tmp_vault, "feedback", "marked-draft",
        tags=["needs-review", "git"],
        sources=["bots/a/m.md"],
    )
    assert read_mnemo_rule(tmp_vault, "marked-draft") is None


def test_read_mnemo_rule_skips_project_type(tmp_vault):
    """Decision #2: project slug must not be readable via the MCP path."""
    _write_page(
        tmp_vault, "project", "my-project",
        tags=["auto-promoted"],
        sources=["bots/a/memory/project_my.md"],
    )
    assert read_mnemo_rule(tmp_vault, "my-project") is None


def test_read_mnemo_rule_finds_rule_in_user_type(tmp_vault):
    _write_page(
        tmp_vault, "user", "prefers-vim",
        tags=["auto-promoted", "editor"],
        sources=["bots/a/m.md"],
        body="Vim is the way.\n",
    )

    result = read_mnemo_rule(tmp_vault, "prefers-vim")
    assert result is not None
    assert result["type"] == "user"
    assert result["body"] == "Vim is the way.\n"


# --- get_mnemo_topics ---


def test_get_mnemo_topics_unions_across_types(tmp_vault):
    _write_page(
        tmp_vault, "feedback", "f1",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],
    )
    _write_page(
        tmp_vault, "user", "u1",
        tags=["auto-promoted", "typing"],
        sources=["bots/a/m.md"],
    )
    _write_page(
        tmp_vault, "reference", "r1",
        tags=["auto-promoted", "api"],
        sources=["bots/a/m.md"],
    )

    assert get_mnemo_topics(tmp_vault) == ["api", "git", "typing"]


def test_get_mnemo_topics_excludes_project_tags(tmp_vault):
    """Decision #2 again: project tags don't pollute the topic list."""
    _write_page(
        tmp_vault, "feedback", "f1",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],
    )
    _write_page(
        tmp_vault, "project", "p1",
        tags=["auto-promoted", "should-not-appear"],
        sources=["bots/a/m.md"],
    )

    topics = get_mnemo_topics(tmp_vault)
    assert "git" in topics
    assert "should-not-appear" not in topics


def test_get_mnemo_topics_empty_vault_returns_empty_list(tmp_vault):
    assert get_mnemo_topics(tmp_vault) == []


def test_get_mnemo_topics_dedupes_across_types(tmp_vault):
    _write_page(
        tmp_vault, "feedback", "f1",
        tags=["auto-promoted", "shared-tag"],
        sources=["bots/a/m.md"],
    )
    _write_page(
        tmp_vault, "user", "u1",
        tags=["auto-promoted", "shared-tag"],
        sources=["bots/a/m.md"],
    )

    topics = get_mnemo_topics(tmp_vault)
    assert topics.count("shared-tag") == 1
