"""End-to-end integration tests for the PreToolUse rule activation stack.

These tests wire the real hook entry point (mnemo.hooks.pre_tool_use.main)
together with the on-disk index produced by rule_activation.build_index +
write_index, and verify the full deny / enrich envelopes plus their
observability logs.

Stub surface:
  - sys.stdin / sys.stdout (so we can feed payloads + capture envelopes)
  - mnemo.core.config.load_config (so we can flip enforcement / enrichment
    on without touching the user's real config file)

Everything else — index loading, glob/regex matching, project resolution,
log writing — runs unmocked through the real code paths.

Test 4 (`test_extraction_rebuilds_index_end_to_end`) is intentionally NOT
duplicated here: tests/integration/test_extract_pipeline.py already has
``test_extraction_rebuilds_rule_activation_index`` which covers exactly
this end-to-end flow against a live ``run_extraction`` call. See the
plan reference in the Task 8 brief.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from mnemo.core.rule_activation import build_index, write_index


# ---------------------------------------------------------------------------
# Helpers — kept small and explicit so each test reads top-to-bottom
# ---------------------------------------------------------------------------


def _write_enforce_rule(
    vault: Path,
    filename: str,
    *,
    project: str,
    deny_pattern: str,
    reason: str,
) -> Path:
    """Write a feedback rule with a single deny_pattern under bots/<project>/."""
    target_dir = vault / "shared" / "feedback"
    target_dir.mkdir(parents=True, exist_ok=True)
    slug = filename.replace(".md", "")
    content = (
        "---\n"
        f"name: {slug}\n"
        f"slug: {slug}\n"
        "stability: stable\n"
        "tags:\n"
        "  - auto-promoted\n"
        "sources:\n"
        f"  - bots/{project}/memory/source.md\n"
        "enforce:\n"
        "  tool: Bash\n"
        f"  deny_pattern: \"{deny_pattern}\"\n"
        f"  reason: \"{reason}\"\n"
        "---\n\n"
        "**Why:** policy enforcement.\n"
    )
    path = target_dir / filename
    path.write_text(content, encoding="utf-8")
    return path


def _write_enrich_rule(
    vault: Path,
    filename: str,
    *,
    project: str,
    path_glob: str,
    tools: str,
    body: str,
) -> Path:
    target_dir = vault / "shared" / "feedback"
    target_dir.mkdir(parents=True, exist_ok=True)
    slug = filename.replace(".md", "")
    content = (
        "---\n"
        f"name: {slug}\n"
        f"slug: {slug}\n"
        "stability: stable\n"
        "tags:\n"
        "  - auto-promoted\n"
        "sources:\n"
        f"  - bots/{project}/memory/source.md\n"
        "activates_on:\n"
        f"  tools: [{tools}]\n"
        f"  path_globs: [\"{path_glob}\"]\n"
        "---\n\n"
        f"{body}\n"
    )
    path = target_dir / filename
    path.write_text(content, encoding="utf-8")
    return path


def _make_git_project(vault: Path, project: str) -> Path:
    """Create vault/bots/<project>/.git/ so resolve_agent maps cwd → project."""
    project_dir = vault / "bots" / project
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".git").mkdir(exist_ok=True)
    return project_dir


def _cfg(vault: Path, *, enf: bool = False, enr: bool = False) -> dict:
    return {
        "vaultRoot": str(vault),
        "enforcement": {"enabled": enf, "log": {"maxBytes": 1_048_576}},
        "enrichment": {
            "enabled": enr,
            "maxRulesPerCall": 3,
            "bodyPreviewChars": 300,
            "log": {"maxBytes": 1_048_576},
        },
    }


def _run_real_hook(monkeypatch, payload: dict, cfg: dict) -> tuple[int, str]:
    """Invoke the REAL mnemo.hooks.pre_tool_use.main() with stubbed I/O + config."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: cfg)

    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    # Import inside the call so the lazy-import path inside main() is
    # exercised against the patched config.
    from mnemo.hooks.pre_tool_use import main

    rc = main()
    return rc, buf.getvalue()


# ---------------------------------------------------------------------------
# Test 1 — full enforce loop: build index → hook denies → log appended
# ---------------------------------------------------------------------------


def test_enforce_full_loop(tmp_vault: Path, monkeypatch):
    project = "project-alpha"
    _write_enforce_rule(
        tmp_vault,
        "no-coauthored.md",
        project=project,
        deny_pattern="git commit.*Co-Authored-By",
        reason="No Co-Authored-By trailers in commits",
    )
    project_dir = _make_git_project(tmp_vault, project)

    # Build + persist the index against the real on-disk vault.
    write_index(tmp_vault, build_index(tmp_vault))

    payload = {
        "tool_name": "Bash",
        "tool_input": {
            "command": "git commit -m 'msg\n\nCo-Authored-By: bot'",
        },
        "cwd": str(project_dir),
    }
    rc, out = _run_real_hook(monkeypatch, payload, _cfg(tmp_vault, enf=True))

    assert rc == 0
    assert out, "Expected a deny envelope on stdout"

    envelope = json.loads(out)
    hook_out = envelope["hookSpecificOutput"]
    assert hook_out["hookEventName"] == "PreToolUse"
    assert hook_out["permissionDecision"] == "deny"
    # reason starts with the rule's reason text; may include path + fix hint suffix
    assert hook_out["permissionDecisionReason"].startswith("No Co-Authored-By trailers in commits")

    # Denial log written end-to-end
    denial_log = tmp_vault / ".mnemo" / "denial-log.jsonl"
    assert denial_log.exists(), "denial-log.jsonl should have been created"
    lines = denial_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["slug"] == "no-coauthored"
    assert entry["project"] == project
    assert entry["tool"] == "Bash"
    assert "Co-Authored-By" in entry["command"]


# ---------------------------------------------------------------------------
# Test 2 — full enrich loop: build index → hook enriches → log appended
# ---------------------------------------------------------------------------


def test_enrich_full_loop(tmp_vault: Path, monkeypatch):
    project = "project-bravo"
    body = "Always add a11y attributes to modal components."
    _write_enrich_rule(
        tmp_vault,
        "modal-a11y.md",
        project=project,
        path_glob="**/*modal*.tsx",
        tools="Edit",
        body=body,
    )
    project_dir = _make_git_project(tmp_vault, project)

    write_index(tmp_vault, build_index(tmp_vault))

    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "src/components/user-modal.tsx"},
        "cwd": str(project_dir),
    }
    rc, out = _run_real_hook(monkeypatch, payload, _cfg(tmp_vault, enr=True))

    assert rc == 0
    assert out, "Expected an additionalContext envelope on stdout"

    envelope = json.loads(out)
    hook_out = envelope["hookSpecificOutput"]
    assert hook_out["hookEventName"] == "PreToolUse"
    assert "additionalContext" in hook_out
    ctx = hook_out["additionalContext"]
    assert "modal-a11y" in ctx, ctx
    assert "a11y attributes" in ctx, ctx

    # Enrichment log written end-to-end
    enrich_log = tmp_vault / ".mnemo" / "enrichment-log.jsonl"
    assert enrich_log.exists(), "enrichment-log.jsonl should have been created"
    lines = enrich_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert "modal-a11y" in entry["hit_slugs"]


# ---------------------------------------------------------------------------
# Test 3 — CRITICAL: per-project segregation must NEVER leak across projects
#
# This is the non-negotiable regression test. It pins the per-project scope
# guarantee called out in the v0.5 plan and the user's auto-memory: a rule
# attached only to project A must NOT fire when the same Bash command is
# issued from inside project B's git root.
#
# It must verify the guarantee at the HOOK level (full pre_tool_use.main),
# not just at the match function. If a future refactor accidentally drops
# the projects_for_rule scoping, BOTH directions of this test should fail
# loudly.
# ---------------------------------------------------------------------------


def test_per_project_segregation(tmp_vault: Path, monkeypatch):
    project_a = "project-a"
    project_b = "project-b"

    # Rule attached ONLY to project-a (sources path under bots/project-a/).
    _write_enforce_rule(
        tmp_vault,
        "no-force-push.md",
        project=project_a,
        deny_pattern="git push.*--force",
        reason="No force pushes in project-a",
    )

    # Both projects exist as git roots so resolve_agent maps cleanly.
    project_a_dir = _make_git_project(tmp_vault, project_a)
    project_b_dir = _make_git_project(tmp_vault, project_b)

    write_index(tmp_vault, build_index(tmp_vault))

    # Sanity — index must contain project-a but NOT project-b.
    index_path = tmp_vault / ".mnemo" / "rule-activation-index.json"
    index_data = json.loads(index_path.read_text(encoding="utf-8"))
    rules_with_enforce_for_a = [
        slug for slug, rule in index_data.get("rules", {}).items()
        if rule.get("enforce") and project_a in rule.get("projects", [])
    ]
    assert rules_with_enforce_for_a, f"{project_a} has no enforce rules in index"
    rules_with_enforce_for_b = [
        slug for slug, rule in index_data.get("rules", {}).items()
        if rule.get("enforce") and project_b in rule.get("projects", [])
    ]
    assert not rules_with_enforce_for_b, f"{project_b} unexpectedly has enforce rules"

    matching_command = "git push --force origin main"

    # --- Invocation 1: from project-b cwd → must NOT fire ---
    payload_b = {
        "tool_name": "Bash",
        "tool_input": {"command": matching_command},
        "cwd": str(project_b_dir),
    }
    rc_b, out_b = _run_real_hook(monkeypatch, payload_b, _cfg(tmp_vault, enf=True))
    assert rc_b == 0
    assert out_b == "", (
        "REGRESSION: rule attached to project-a fired from project-b cwd. "
        "Per-project scope leaked. Did someone drop the projects_for_rule "
        "filter in rule_activation or pre_tool_use?"
    )

    denial_log = tmp_vault / ".mnemo" / "denial-log.jsonl"
    assert not denial_log.exists() or denial_log.read_text(encoding="utf-8") == "", (
        "REGRESSION: denial log was written for a project-b invocation"
    )

    # --- Invocation 2: from project-a cwd → MUST fire ---
    payload_a = {
        "tool_name": "Bash",
        "tool_input": {"command": matching_command},
        "cwd": str(project_a_dir),
    }
    rc_a, out_a = _run_real_hook(monkeypatch, payload_a, _cfg(tmp_vault, enf=True))
    assert rc_a == 0
    assert out_a, (
        "REGRESSION: rule attached to project-a did NOT fire from project-a cwd. "
        "The hook is over-filtering — per-project scope is broken."
    )
    envelope = json.loads(out_a)
    assert envelope["hookSpecificOutput"]["permissionDecision"] == "deny"
    # reason starts with the rule's reason text; may include path + fix hint suffix
    assert envelope["hookSpecificOutput"]["permissionDecisionReason"].startswith(
        "No force pushes in project-a"
    )

    # And the denial log should now contain exactly one entry, for project-a.
    assert denial_log.exists()
    lines = denial_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["project"] == project_a
    assert entry["slug"] == "no-force-push"
