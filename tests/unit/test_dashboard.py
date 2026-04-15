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


def test_update_home_md_excludes_needs_review(tmp_path: Path) -> None:
    _write_page(tmp_path, "feedback", "pending",
                sources=["a"],
                tags=["needs-review", "auth"])
    update_home_md(_cfg(tmp_path))
    text = (tmp_path / "HOME.md").read_text()
    assert "pending" not in text


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
