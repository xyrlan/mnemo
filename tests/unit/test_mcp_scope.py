"""Tests for v0.7 scope semantics on MCP retrieval."""
from __future__ import annotations

import pytest

from mnemo.core.mcp.tools import _rule_in_scope


def _rule(projects, universal=False):
    return {"projects": list(projects), "universal": universal}


def test_scope_project_matches_local_rule():
    assert _rule_in_scope(_rule(["alpha"]), "alpha", "project") is True


def test_scope_project_matches_universal_rule_in_other_project():
    assert _rule_in_scope(_rule(["alpha"], universal=True), "beta", "project") is True


def test_scope_project_excludes_local_rule_of_other_project():
    assert _rule_in_scope(_rule(["alpha"]), "beta", "project") is False


def test_scope_local_only_excludes_universal():
    assert _rule_in_scope(_rule(["alpha"], universal=True), "beta", "local-only") is False


def test_scope_local_only_matches_local_rule():
    assert _rule_in_scope(_rule(["alpha"]), "alpha", "local-only") is True


def test_scope_vault_matches_everything():
    assert _rule_in_scope(_rule([]), None, "vault") is True
    assert _rule_in_scope(_rule(["alpha"]), "beta", "vault") is True
    assert _rule_in_scope(_rule(["alpha"], universal=True), None, "vault") is True


def test_scope_project_with_none_project_falls_through():
    # When project cannot be resolved, scope="project" returns universal rules only.
    assert _rule_in_scope(_rule([], universal=True), None, "project") is True
    assert _rule_in_scope(_rule(["alpha"]), None, "project") is False
