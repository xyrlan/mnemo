"""projects_for_rule must derive projects from frontmatter when sources[] is empty
or non-bots; existing sources-path behavior must stay intact."""
from __future__ import annotations

from mnemo.core.rule_activation.index import projects_for_rule


def test_sources_under_bots_still_wins():
    assert projects_for_rule(["bots/mnemo/briefings/sessions/x.md"]) == ["mnemo"]


def test_existing_callers_without_frontmatter_kwarg_unchanged():
    # Regression guard: the positional-only legacy signature must keep working.
    assert projects_for_rule([]) == []
    assert projects_for_rule(["bots/mnemo/x.md"]) == ["mnemo"]


def test_frontmatter_project_used_when_sources_empty():
    assert projects_for_rule([], frontmatter={"project": "mnemo"}) == ["mnemo"]


def test_frontmatter_projects_list_used_when_sources_empty():
    assert projects_for_rule([], frontmatter={"projects": ["mnemo", "Meunu"]}) == ["Meunu", "mnemo"]


def test_frontmatter_ignored_when_sources_yield_bots_paths():
    assert projects_for_rule(["bots/mnemo/x.md"], frontmatter={"project": "wrong"}) == ["mnemo"]


def test_non_bots_sources_fall_back_to_frontmatter():
    assert projects_for_rule(["shared/feedback/x.md"], frontmatter={"project": "mnemo"}) == ["mnemo"]


def test_empty_everything_returns_empty():
    assert projects_for_rule([], frontmatter={}) == []
    assert projects_for_rule([], frontmatter=None) == []


def test_frontmatter_projects_list_filters_non_strings_and_empties():
    assert projects_for_rule(
        [], frontmatter={"projects": ["mnemo", "", None, 42, "Meunu"]}
    ) == ["Meunu", "mnemo"]


def test_mixed_sources_bots_hit_still_wins_over_frontmatter():
    assert projects_for_rule(
        ["shared/x.md", "bots/mnemo/y.md"],
        frontmatter={"project": "wrong"},
    ) == ["mnemo"]


def test_absolute_path_with_bots_segment_is_honored():
    assert projects_for_rule(["/home/xyrlan/mnemo/bots/mnemo/memory/x.md"]) == ["mnemo"]


def test_absolute_path_without_bots_segment_ignored():
    # Guard: no bots component anywhere → falls back to frontmatter (empty here).
    assert projects_for_rule(["/home/xyrlan/mnemo/shared/feedback/x.md"]) == []


def test_mixed_relative_and_absolute_paths_both_counted():
    assert sorted(projects_for_rule([
        "/home/xyrlan/mnemo/bots/Meunu/x.md",
        "bots/mnemo/y.md",
    ])) == ["Meunu", "mnemo"]
