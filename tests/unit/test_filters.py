"""Unit tests for the shared filter predicate (v0.4).

The filter is the single source of truth for "consumer-visible" pages, consumed
by the HOME dashboard (v0.4) and the MCP tools (v0.5). See
project_mnemo_v0.4_direction.md "Shared filter specification".
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.filters import (
    MANAGED_TAGS,
    derive_rule_slug,
    is_consumer_visible,
    parse_frontmatter,
    topic_tags,
)


def test_derive_rule_slug_prefers_explicit_slug() -> None:
    assert derive_rule_slug({"slug": "my-slug", "name": "Display"}, "stem") == "my-slug"


def test_derive_rule_slug_falls_back_to_name_when_slug_absent() -> None:
    assert derive_rule_slug({"name": "Display Name"}, "stem") == "Display Name"


def test_derive_rule_slug_uses_stem_when_both_absent() -> None:
    assert derive_rule_slug({}, "file-stem") == "file-stem"


def test_derive_rule_slug_treats_empty_string_as_absent() -> None:
    """Regression: ``slug: ""`` (migration artefact) must not hijack the
    identifier and silently shadow the real name/stem."""
    assert derive_rule_slug({"slug": "", "name": "Real Name"}, "stem") == "Real Name"
    assert derive_rule_slug({"slug": "   ", "name": "Real Name"}, "stem") == "Real Name"
    assert derive_rule_slug({"slug": "", "name": ""}, "stem") == "stem"


def test_derive_rule_slug_ignores_non_string_values() -> None:
    """A list/None in the field — e.g. YAML deserialization edge — must not raise."""
    assert derive_rule_slug({"slug": None, "name": "Real"}, "stem") == "Real"
    assert derive_rule_slug({"slug": ["wrong"], "name": None}, "stem") == "stem"


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


# --- nested dict tests ---


def test_parse_frontmatter_nested_dict_with_scalars() -> None:
    text = (
        "---\n"
        "enforce:\n"
        "  tool: Bash\n"
        "  deny_pattern: 'git commit.*Co-Authored-By'\n"
        "  reason: No Co-Authored-By trailers\n"
        "---\n"
        "body\n"
    )
    fm = parse_frontmatter(text)
    assert isinstance(fm["enforce"], dict)
    assert fm["enforce"]["tool"] == "Bash"
    assert fm["enforce"]["deny_pattern"] == "git commit.*Co-Authored-By"
    assert fm["enforce"]["reason"] == "No Co-Authored-By trailers"


def test_parse_frontmatter_nested_dict_with_inline_list() -> None:
    text = (
        "---\n"
        "activates_on:\n"
        "  tools: [Edit, Write, MultiEdit]\n"
        "---\n"
        "body\n"
    )
    fm = parse_frontmatter(text)
    assert isinstance(fm["activates_on"], dict)
    assert fm["activates_on"]["tools"] == ["Edit", "Write", "MultiEdit"]


def test_parse_frontmatter_nested_dict_with_block_list() -> None:
    text = (
        "---\n"
        "activates_on:\n"
        "  path_globs:\n"
        "    - '**/*modal*.tsx'\n"
        "    - 'src/app/**/modals/**'\n"
        "---\n"
        "body\n"
    )
    fm = parse_frontmatter(text)
    assert isinstance(fm["activates_on"], dict)
    assert fm["activates_on"]["path_globs"] == [
        "**/*modal*.tsx",
        "src/app/**/modals/**",
    ]


def test_parse_frontmatter_multiple_nested_dicts() -> None:
    text = (
        "---\n"
        "enforce:\n"
        "  tool: Bash\n"
        "  deny_pattern: 'git commit.*Co-Authored-By'\n"
        "  reason: No Co-Authored-By trailers\n"
        "activates_on:\n"
        "  tools: [Edit, Write, MultiEdit]\n"
        "---\n"
        "body\n"
    )
    fm = parse_frontmatter(text)
    assert fm["enforce"]["tool"] == "Bash"
    assert fm["enforce"]["deny_pattern"] == "git commit.*Co-Authored-By"
    assert fm["activates_on"]["tools"] == ["Edit", "Write", "MultiEdit"]


def test_parse_frontmatter_flat_and_nested_coexist() -> None:
    text = (
        "---\n"
        "name: my-rule\n"
        "description: a thing\n"
        "enforce:\n"
        "  tool: Bash\n"
        "  reason: no trailers\n"
        "tags:\n"
        "  - auto-promoted\n"
        "---\n"
        "body\n"
    )
    fm = parse_frontmatter(text)
    assert fm["name"] == "my-rule"
    assert fm["description"] == "a thing"
    assert isinstance(fm["enforce"], dict)
    assert fm["enforce"]["tool"] == "Bash"
    assert fm["enforce"]["reason"] == "no trailers"
    assert fm["tags"] == ["auto-promoted"]


def test_parse_frontmatter_malformed_nested_falls_through() -> None:
    # bare key: with no indented children and no following block-list lines
    text = (
        "---\n"
        "name: x\n"
        "orphan:\n"
        "description: y\n"
        "---\n"
        "body\n"
    )
    fm = parse_frontmatter(text)
    # should not crash; orphan gets an empty dict (safe fallback)
    assert fm["name"] == "x"
    assert fm["description"] == "y"
    assert fm["orphan"] == {}


def test_parse_frontmatter_double_nested_is_dropped_not_leaked() -> None:
    # deeply-indented keys (3+ spaces) must be silently dropped, not leaked
    # to the top level
    text = (
        "---\n"
        "outer:\n"
        "  inner:\n"
        "    key: value\n"
        "description: ok\n"
        "---\n"
        "body\n"
    )
    fm = parse_frontmatter(text)
    # the inner 'key' must NOT appear at the top level
    assert "key" not in fm
    # outer is present in some safe shape
    assert "outer" in fm
    # sibling top-level key is still parsed
    assert fm["description"] == "ok"


def test_parse_frontmatter_dequotes_scalars_and_lists() -> None:
    text = (
        "---\n"
        "single_quoted: 'foo'\n"
        "double_quoted: \"foo\"\n"
        "mismatched: 'bar\n"
        "empty_quoted: ''\n"
        "enforce:\n"
        "  deny_pattern: 'x'\n"
        "activates_on:\n"
        "  path_globs:\n"
        "    - 'x'\n"
        "---\n"
        "body\n"
    )
    fm = parse_frontmatter(text)
    # single-quoted flat scalar
    assert fm["single_quoted"] == "foo"
    # double-quoted flat scalar
    assert fm["double_quoted"] == "foo"
    # mismatched quotes — left untouched
    assert fm["mismatched"] == "'bar"
    # empty quoted string
    assert fm["empty_quoted"] == ""
    # quoted nested subkey scalar
    assert fm["enforce"]["deny_pattern"] == "x"
    # quoted block list item
    assert fm["activates_on"]["path_globs"] == ["x"]
