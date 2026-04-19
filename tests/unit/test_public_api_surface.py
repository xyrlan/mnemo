"""Public API surface freeze — gate for every Wave 3 package-shim PR.

PR 0 of the v0.9 refactor roadmap. See
``docs/superpowers/plans/2026-04-19-refactor-roadmap.md``.

Wave 3 PRs (F, G, H, I) convert these four modules into packages with a
back-compat shim in ``__init__.py``. A missed re-export is a silent
``ImportError`` waiting to fire at runtime; this test is the guardrail.

Enumeration methodology (frozen 2026-04-19 against master @ af86ce6): grep
``src/`` and ``tests/`` for ``from <module> import ...`` AND for attribute
access on module-imported aliases (e.g. ``inbox.X``, ``prompts.X``,
``rule_activation.X`` / ``ra.X``). Both forms break if a shim forgets a name.

When a Wave 3 PR legitimately removes a name (e.g. PR G unifies
``_describe_enforce_error`` / ``_describe_enrich_error`` into a single
``parse_block`` walker), delete it from the corresponding tuple here in the
same PR and update the caller(s).
"""
from __future__ import annotations

import importlib

import pytest


SURFACE: dict[str, tuple[str, ...]] = {
    "mnemo.cli": (
        "main",
        "COMMANDS",
        "_resolve_vault",  # monkeypatched: test_cli_recall, test_cli_telemetry
    ),
    "mnemo.core.rule_activation": (
        # index
        "INDEX_VERSION",
        "build_index",
        "load_index",
        "write_index",
        "projects_for_rule",
        "_is_universal",  # imported by reflex/index.py and tests
        # parsing
        "parse_enforce_block",
        "parse_activates_on_block",
        "_describe_enforce_error",  # cli.py:771
        "_describe_enrich_error",   # cli.py:771
        # matching
        "EnforceHit",
        "EnrichHit",
        "match_bash_enforce",
        "match_path_enrich",
        "normalize_bash_command",
        "_glob_matches",   # test_rule_activation_match
        "_glob_to_regex",  # test_rule_activation_match
        # per-project iteration
        "iter_enforce_rules_for_project",
        "iter_enrich_rules_for_project",
        # activity log
        "log_denial",
        "log_enrichment",
        # re-exported filter (patched via this module's path in tests)
        "is_consumer_visible",
    ),
    "mnemo.core.extract.inbox": (
        "ApplyResult",
        "ExtractedPage",
        "ExtractionIOError",
        "StateSchemaError",
        "apply_pages",
        "dedupe_by_slug",
        "atomic_write_state",
        "load_state",
        # private helpers imported by promote.py and tests
        "_atomic_write",
        "_file_hash",
        "_target_path_for_page",
        "_is_auto_promoted_target",
        "_sibling_path",
        "_render_page",
        "_bodies_similar",
        "_extract_body",
        "_stem_slug",
    ),
    "mnemo.core.extract.prompts": (
        "BRIEFING_SYSTEM_PROMPT",
        "FEEDBACK_SYSTEM_PROMPT",
        "USER_SYSTEM_PROMPT",
        "REFERENCE_SYSTEM_PROMPT",
        "build_briefing_prompt",
        "build_feedback_prompt",
        "build_user_prompt",
        "build_reference_prompt",
        "chunks_for",
    ),
}


@pytest.mark.parametrize(
    ("module_path", "name"),
    [(mod, name) for mod, names in SURFACE.items() for name in names],
    ids=[f"{mod}::{name}" for mod, names in SURFACE.items() for name in names],
)
def test_public_api_surface(module_path: str, name: str) -> None:
    module = importlib.import_module(module_path)
    assert hasattr(module, name), (
        f"{module_path} must expose '{name}' — missing re-export in shim? "
        "See docs/superpowers/plans/2026-04-19-refactor-roadmap.md PR 0."
    )
