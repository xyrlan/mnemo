"""Unit tests for core/dashboard.py (v0.4 HOME.md dashboard)."""
from __future__ import annotations

from pathlib import Path

from mnemo.core import dashboard
from mnemo.core.dashboard import BLOCK_BEGIN, BLOCK_END, update_home_md


def _cfg(vault_root: Path) -> dict:
    return {"vaultRoot": str(vault_root)}


def _write_page(
    vault_root: Path,
    page_type: str,
    slug: str,
    *,
    sources: list[str],
    tags: list[str],
    stability: str = "stable",
    inbox: bool = False,
    name: str | None = None,
) -> None:
    base = "shared/_inbox" if inbox else "shared"
    d = vault_root / base / page_type
    d.mkdir(parents=True, exist_ok=True)
    src_yaml = "\n".join(f"  - {s}" for s in sources)
    tag_yaml = "\n".join(f"  - {t}" for t in tags)
    (d / f"{slug}.md").write_text(
        "---\n"
        f"name: {name or slug}\n"
        f"description: d\n"
        f"type: {page_type}\n"
        f"stability: {stability}\n"
        "sources:\n"
        f"{src_yaml}\n"
        "tags:\n"
        f"{tag_yaml}\n"
        "---\n\n"
        f"body of {slug}\n"
    )


def test_update_home_md_creates_home_when_missing(tmp_path: Path) -> None:
    _write_page(tmp_path, "feedback", "use-yarn",
                sources=["bots/a/memory/x.md"],
                tags=["auto-promoted", "package-management"])
    out = update_home_md(_cfg(tmp_path))
    assert out == tmp_path / "HOME.md"
    assert out.exists()
    text = out.read_text()
    assert BLOCK_BEGIN in text
    assert BLOCK_END in text
    assert "use-yarn" in text
    assert "package-management" in text


def test_update_home_md_groups_multi_source_under_cross_agent_section(tmp_path: Path) -> None:
    _write_page(tmp_path, "feedback", "merged-rule",
                sources=["bots/a/memory/x.md", "bots/b/memory/y.md"],
                tags=["auto-promoted", "git"])
    _write_page(tmp_path, "feedback", "solo-rule",
                sources=["bots/a/memory/z.md"],
                tags=["auto-promoted", "react"])
    update_home_md(_cfg(tmp_path))
    text = (tmp_path / "HOME.md").read_text()
    multi_idx = text.find("Cross-agent synthesized rules")
    single_idx = text.find("Auto-promoted direct reformats")
    assert multi_idx != -1
    assert single_idx != -1
    assert multi_idx < single_idx
    merged_pos = text.find("merged-rule", multi_idx)
    solo_pos = text.find("solo-rule", single_idx)
    assert merged_pos != -1 and merged_pos < single_idx
    assert solo_pos != -1


def test_update_home_md_excludes_inbox_drafts(tmp_path: Path) -> None:
    _write_page(tmp_path, "feedback", "draft",
                sources=["a", "b"],
                tags=["needs-review", "auth"],
                inbox=True)
    _write_page(tmp_path, "feedback", "visible",
                sources=["a"],
                tags=["auto-promoted", "workflow"])
    update_home_md(_cfg(tmp_path))
    text = (tmp_path / "HOME.md").read_text()
    assert "visible" in text
    assert "draft" not in text


def test_update_home_md_includes_hand_promoted_needs_review(tmp_path: Path) -> None:
    """Regression (v0.18): the tag is stale once the page is in shared/<type>/.

    The user moved it there; the dashboard must show it. ``topic_tags`` still
    strips the marker, so it must not appear as a topic heading.
    """
    _write_page(tmp_path, "feedback", "pending",
                sources=["a"],
                tags=["needs-review", "auth"])
    update_home_md(_cfg(tmp_path))
    text = (tmp_path / "HOME.md").read_text()
    assert "pending" in text
    assert "#needs-review" not in text


def test_update_home_md_excludes_evolving(tmp_path: Path) -> None:
    _write_page(tmp_path, "feedback", "unsettled",
                sources=["a"],
                tags=["auto-promoted", "state-management"],
                stability="evolving")
    update_home_md(_cfg(tmp_path))
    text = (tmp_path / "HOME.md").read_text()
    assert "unsettled" not in text


def test_update_home_md_renders_by_topic_section(tmp_path: Path) -> None:
    _write_page(tmp_path, "feedback", "rule-a",
                sources=["a"], tags=["auto-promoted", "git"])
    _write_page(tmp_path, "feedback", "rule-b",
                sources=["a"], tags=["auto-promoted", "git", "workflow"])
    _write_page(tmp_path, "feedback", "rule-c",
                sources=["a"], tags=["auto-promoted", "react"])
    update_home_md(_cfg(tmp_path))
    text = (tmp_path / "HOME.md").read_text()
    assert "### By topic" in text
    assert "#### #git" in text
    assert "#### #react" in text
    assert "#### #workflow" in text


def test_update_home_md_replaces_existing_block(tmp_path: Path) -> None:
    # Pre-existing HOME with a stale block and user content below
    home = tmp_path / "HOME.md"
    home.write_text(
        "---\n"
        "tags: [home, dashboard]\n"
        "---\n"
        "# Welcome\n"
        "\n"
        f"{BLOCK_BEGIN}\n"
        "## STALE DASHBOARD\n"
        "- outdated stuff\n"
        f"{BLOCK_END}\n"
        "\n"
        "## My personal notes\n"
        "Random thoughts the user wrote.\n"
    )
    _write_page(tmp_path, "feedback", "new-rule",
                sources=["a"], tags=["auto-promoted", "git"])
    update_home_md(_cfg(tmp_path))
    text = home.read_text()
    assert "STALE DASHBOARD" not in text
    assert "new-rule" in text
    # User content below the block is preserved
    assert "My personal notes" in text
    assert "Random thoughts the user wrote" in text


def test_update_home_md_appends_block_when_missing_preserving_user_content(tmp_path: Path) -> None:
    # User has HOME.md but no block (fresh v0.3.1 install, now upgrading)
    home = tmp_path / "HOME.md"
    home.write_text(
        "---\n"
        "tags: [home, dashboard]\n"
        "---\n"
        "# My vault\n"
        "\n"
        "Here is some user-authored landing content.\n"
    )
    _write_page(tmp_path, "feedback", "new-rule",
                sources=["a"], tags=["auto-promoted", "git"])
    update_home_md(_cfg(tmp_path))
    text = home.read_text()
    assert BLOCK_BEGIN in text
    assert "new-rule" in text
    assert "Here is some user-authored landing content" in text
    # Block sits above the user content
    assert text.find(BLOCK_BEGIN) < text.find("Here is some user-authored landing content")


def test_update_home_md_empty_vault_shows_placeholder(tmp_path: Path) -> None:
    update_home_md(_cfg(tmp_path))
    text = (tmp_path / "HOME.md").read_text()
    assert BLOCK_BEGIN in text
    assert "No consumer-visible pages yet" in text


def test_update_home_md_is_idempotent_on_second_call(tmp_path: Path) -> None:
    """Back-to-back calls should produce near-identical output (only timestamp differs)."""
    _write_page(tmp_path, "feedback", "x", sources=["a"], tags=["auto-promoted", "git"])
    update_home_md(_cfg(tmp_path))
    first = (tmp_path / "HOME.md").read_text()
    update_home_md(_cfg(tmp_path))
    second = (tmp_path / "HOME.md").read_text()
    # Strip timestamps for comparison
    import re
    pattern = re.compile(r"_Last updated: [^_]+_")
    assert pattern.sub("_TS_", first) == pattern.sub("_TS_", second)


def test_update_home_md_path_qualified_wikilinks(tmp_path: Path) -> None:
    _write_page(tmp_path, "feedback", "slug-a", sources=["a"],
                tags=["auto-promoted", "git"])
    update_home_md(_cfg(tmp_path))
    text = (tmp_path / "HOME.md").read_text()
    assert "[[shared/feedback/slug-a]]" in text


def test_update_home_md_multi_source_sorts_before_single(tmp_path: Path) -> None:
    _write_page(tmp_path, "feedback", "solo",
                sources=["a"], tags=["auto-promoted", "git"])
    _write_page(tmp_path, "feedback", "merged",
                sources=["a", "b", "c"], tags=["auto-promoted", "git"])
    update_home_md(_cfg(tmp_path))
    text = (tmp_path / "HOME.md").read_text()
    assert text.find("merged") < text.find("solo")


def test_update_home_md_no_existing_block_but_no_frontmatter(tmp_path: Path) -> None:
    home = tmp_path / "HOME.md"
    home.write_text("# Just a heading\n\nuser content\n")
    _write_page(tmp_path, "feedback", "r", sources=["a"],
                tags=["auto-promoted", "git"])
    update_home_md(_cfg(tmp_path))
    text = home.read_text()
    assert BLOCK_BEGIN in text
    assert "user content" in text
    assert text.find(BLOCK_BEGIN) < text.find("user content")


def test_dashboard_caps_high_trust_section(tmp_path: Path) -> None:
    """Uncapped, the dashboard listed every rule in the vault (5848 wikilinks
    on a 1731-page vault). HOME.md then links to ~everything, which turns the
    graph into one hub with an edge to every node and hides all real structure.

    The high-trust tier is small in practice, but cap it anyway and say how
    many were dropped."""
    for i in range(dashboard.MAX_HIGH_TRUST + 5):
        _write_page(tmp_path, "reference", f"multi-{i:03d}",
                    sources=["a", "b"], tags=["auto-promoted", "git"])
    update_home_md(_cfg(tmp_path))
    text = (tmp_path / "HOME.md").read_text()
    section = text.split("### ")[1]
    assert section.count("\n- [[") == dashboard.MAX_HIGH_TRUST
    assert "5 more" in text


def test_dashboard_summarizes_single_source_rules_without_listing_them(tmp_path: Path) -> None:
    """`source_count == 1` pages are 98% of a mature vault (2026-09-01 audit).
    Listing them is what made HOME.md 6600 lines, and it adds no signal — one
    source is exactly the tier the reader has least reason to scan.

    Report the count; the rules stay reachable through the topic sections,
    search, and `list_rules_by_topic`."""
    _write_page(tmp_path, "reference", "solo-one",
                sources=["a"], tags=["auto-promoted", "git"])
    _write_page(tmp_path, "reference", "solo-two",
                sources=["a"], tags=["auto-promoted", "git"])
    _write_page(tmp_path, "reference", "trusted",
                sources=["a", "b"], tags=["auto-promoted", "git"])
    update_home_md(_cfg(tmp_path))
    text = (tmp_path / "HOME.md").read_text()

    tier = text.split("### Auto-promoted direct reformats")[1].split("###")[0]
    assert "[[shared/reference/solo-one]]" not in tier
    assert "[[shared/reference/solo-two]]" not in tier
    # The tier is acknowledged, with its size.
    assert "2 rules" in tier
    # They remain reachable under their topic, which is the browsing path.
    assert "[[shared/reference/solo-one]]" in text.split("#### #git")[1]
    # High-trust rules are still linked in their own tier.
    assert "[[shared/reference/trusted]]" in text.split("### Cross-agent")[1]


def test_dashboard_caps_each_topic_bucket(tmp_path: Path) -> None:
    """A topic like #testing holds hundreds of rules. Show the strongest few
    and the bucket size, not the whole bucket."""
    for i in range(dashboard.MAX_PER_TOPIC + 7):
        _write_page(tmp_path, "reference", f"t-{i:03d}",
                    sources=["a"], tags=["auto-promoted", "testing"])
    update_home_md(_cfg(tmp_path))
    text = (tmp_path / "HOME.md").read_text()
    bucket = text.split("#### #testing")[1]
    assert bucket.count("\n- [[") == dashboard.MAX_PER_TOPIC
    # Heading states the true size, so the cap is visible rather than silent.
    assert f"({dashboard.MAX_PER_TOPIC + 7} rules)" in text


def test_dashboard_link_count_stays_bounded_on_a_large_vault(tmp_path: Path) -> None:
    """The actual regression guard: the dashboard's wikilink count must not
    scale with vault size. 300 rules across 3 topics previously produced 600+
    links; it must now be bounded by the caps."""
    topics = ["git", "testing", "react"]
    for i in range(300):
        _write_page(tmp_path, "reference", f"r-{i:03d}",
                    sources=["a"], tags=["auto-promoted", topics[i % 3]])
    update_home_md(_cfg(tmp_path))
    text = (tmp_path / "HOME.md").read_text()
    links = text.count("- [[")
    ceiling = dashboard.MAX_HIGH_TRUST + len(topics) * dashboard.MAX_PER_TOPIC
    assert links <= ceiling, f"dashboard emitted {links} wikilinks, ceiling {ceiling}"
