from __future__ import annotations

from pathlib import Path

from tests.unit._export_fixtures import write_rule


def test_selects_project_rules_and_universal_only(tmp_vault: Path):
    from mnemo.core.export.select import select_rules

    write_rule(tmp_vault, slug="mine", projects=("app",))
    write_rule(tmp_vault, slug="theirs", projects=("other",))
    write_rule(tmp_vault, slug="everywhere", projects=("other", "third"))

    rules = select_rules(tmp_vault, project="app")
    assert [r.slug for r in rules] == ["everywhere", "mine"]
    assert rules[0].universal is True and rules[1].universal is False


def test_default_types_are_feedback_and_user(tmp_vault: Path):
    from mnemo.core.export.select import select_rules

    write_rule(tmp_vault, slug="fb", page_type="feedback")
    write_rule(tmp_vault, slug="us", page_type="user")
    write_rule(tmp_vault, slug="ref", page_type="reference")

    assert {r.slug for r in select_rules(tmp_vault, project="app")} == {"fb", "us"}
    assert {r.slug for r in select_rules(tmp_vault, project="app", types=("reference",))} == {"ref"}


def test_inbox_archive_and_evolving_are_skipped(tmp_vault: Path):
    from mnemo.core.export.select import select_rules

    write_rule(tmp_vault, slug="staged", inbox=True)
    write_rule(tmp_vault, slug="flux", stability="evolving")
    archived = tmp_vault / "shared" / "_archive" / "reclassify-x" / "originals" / "feedback"
    archived.mkdir(parents=True)
    (archived / "old.md").write_text("---\nname: old\nslug: old\ntype: feedback\nsources:\n  - bots/app/b.md\n---\nx\n")
    write_rule(tmp_vault, slug="live")

    assert [r.slug for r in select_rules(tmp_vault, project="app")] == ["live"]


def test_order_universal_then_source_count_then_slug(tmp_vault: Path):
    from mnemo.core.export.select import select_rules

    write_rule(tmp_vault, slug="b-one", projects=("app",))
    write_rule(tmp_vault, slug="a-one", projects=("app",))
    write_rule(tmp_vault, slug="z-two", projects=("app", "app"))          # 2 sources, 1 project
    write_rule(tmp_vault, slug="uni", projects=("app", "other"))          # universal

    assert [r.slug for r in select_rules(tmp_vault, project="app")] == ["uni", "z-two", "a-one", "b-one"]


def test_limit_truncates_after_ordering(tmp_vault: Path):
    from mnemo.core.export.select import select_rules

    write_rule(tmp_vault, slug="b", projects=("app",))
    write_rule(tmp_vault, slug="uni", projects=("app", "other"))
    write_rule(tmp_vault, slug="a", projects=("app",))

    assert [r.slug for r in select_rules(tmp_vault, project="app", limit=2)] == ["uni", "a"]


def test_rule_carries_name_body_quote_and_counts(tmp_vault: Path):
    from mnemo.core.export.select import select_rules

    write_rule(
        tmp_vault, slug="use-yarn-not-npm", name="Use yarn, never npm",
        body="Use yarn in this repo.\n\n**Why:** lockfile.\n",
        quote="never use npm in this repo, always yarn",
    )
    (rule,) = select_rules(tmp_vault, project="app")
    assert rule.name == "Use yarn, never npm"
    assert rule.body == "Use yarn in this repo.\n\n**Why:** lockfile.\n"      # graph section gone
    assert rule.quote == "never use npm in this repo, always yarn"
    assert rule.source_count == 1 and rule.page_type == "feedback"


def test_missing_vault_yields_nothing(tmp_path: Path):
    from mnemo.core.export.select import select_rules

    assert select_rules(tmp_path / "nope", project="app") == []


def test_types_as_bare_string_is_treated_as_one_type(tmp_vault: Path):
    from mnemo.core.export.select import select_rules

    write_rule(tmp_vault, slug="fb", page_type="feedback")

    assert [r.slug for r in select_rules(tmp_vault, project="app", types="feedback")] == ["fb"]


def test_empty_sources_with_frontmatter_project_is_selected(tmp_vault: Path):
    from mnemo.core.export.select import select_rules

    page_dir = tmp_vault / "shared" / "feedback"
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "fb.md").write_text(
        "---\nname: fb\nslug: fb\ntype: feedback\nstability: stable\n"
        "sources: []\nproject: app\n---\nBody.\n",
        encoding="utf-8",
    )

    (rule,) = select_rules(tmp_vault, project="app")
    assert rule.slug == "fb"
    assert rule.source_count == 0


def test_frontmatter_projects_list_goes_universal(tmp_vault: Path):
    from mnemo.core.export.select import select_rules

    page_dir = tmp_vault / "shared" / "feedback"
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "fb.md").write_text(
        "---\nname: fb\nslug: fb\ntype: feedback\nstability: stable\n"
        "sources: []\nprojects:\n  - app\n  - other\n---\nBody.\n",
        encoding="utf-8",
    )

    (rule,) = select_rules(tmp_vault, project="app")
    assert rule.universal is True


def test_limit_zero_returns_empty_and_none_returns_everything(tmp_vault: Path):
    from mnemo.core.export.select import select_rules

    write_rule(tmp_vault, slug="a", projects=("app",))
    write_rule(tmp_vault, slug="b", projects=("app",))

    assert select_rules(tmp_vault, project="app", limit=0) == []
    assert [r.slug for r in select_rules(tmp_vault, project="app", limit=None)] == ["a", "b"]
