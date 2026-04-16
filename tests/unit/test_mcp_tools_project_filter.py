"""Tests for MCP project-filter behavior (v0.5.2)."""
from __future__ import annotations

from mnemo.core.mcp.tools import _rule_belongs_to_project


def test_rule_belongs_single_project_match():
    fm = {"sources": ["bots/mnemo/memory/m.md"]}
    assert _rule_belongs_to_project(fm, "mnemo") is True


def test_rule_belongs_single_project_no_match():
    fm = {"sources": ["bots/other-proj/memory/m.md"]}
    assert _rule_belongs_to_project(fm, "mnemo") is False


def test_rule_belongs_multi_project_sources():
    fm = {"sources": ["bots/alpha/memory/a.md", "bots/beta/memory/b.md"]}
    assert _rule_belongs_to_project(fm, "alpha") is True
    assert _rule_belongs_to_project(fm, "beta") is True
    assert _rule_belongs_to_project(fm, "gamma") is False


def test_rule_belongs_no_sources_returns_false():
    assert _rule_belongs_to_project({}, "mnemo") is False
    assert _rule_belongs_to_project({"sources": []}, "mnemo") is False
    assert _rule_belongs_to_project({"sources": None}, "mnemo") is False


def test_rule_belongs_partial_prefix_no_false_positive():
    """'bots/mn/' must NOT match project 'mnemo'."""
    fm = {"sources": ["bots/mn/memory/m.md"]}
    assert _rule_belongs_to_project(fm, "mnemo") is False
