"""Integration tests for the full extraction pipeline (mocked LLM)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core import llm as llm_mod
from mnemo.core.extract import run_extraction


def _cfg(vault: Path, chunk_size: int = 10) -> dict:
    return {
        "vaultRoot": str(vault),
        "extraction": {
            "model": "claude-haiku-4-5",
            "chunkSize": chunk_size,
            "hintThreshold": 5,
            "preferAPI": False,
            "subprocessTimeout": 60,
            "costSoftCap": None,
        },
    }


def _resp(pages: list[dict]) -> llm_mod.LLMResponse:
    text = json.dumps({"pages": pages})
    return llm_mod.LLMResponse(
        text=text,
        total_cost_usd=0.0,
        input_tokens=500,
        output_tokens=200,
        api_key_source="none",
        raw={"result": text},
    )


@pytest.fixture
def stub_llm_integration(monkeypatch):
    queue: list = []

    def installer(items):
        queue.extend(items)

    def fake_call(prompt, *, system, model, timeout):
        if not queue:
            raise AssertionError("stub_llm: queue exhausted")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(llm_mod, "call", fake_call)
    return installer


def test_full_first_run_produces_expected_layout(populated_vault, stub_llm_integration):
    stub_llm_integration([
        _resp([
            {"slug": "use-yarn", "name": "Use yarn", "description": "d", "type": "feedback",
             "body": "yarn b", "source_files": ["bots/agent-a/memory/feedback_use_yarn.md"]},
            {"slug": "no-commits-without-permission", "name": "No commits", "description": "d",
             "type": "feedback", "body": "nc b",
             "source_files": [
                 "bots/agent-b/memory/feedback_no_commits.md",
                 "bots/agent-b/memory/feedback_no_commit_without_permission.md",
             ]},
        ]),
    ])

    summary = run_extraction(_cfg(populated_vault))

    assert summary.pages_written >= 3  # 1 project + 2 cluster pages
    assert summary.llm_calls == 1
    assert summary.failed_chunks == 0

    # Verify disk layout — v0.3 split: single-source auto-promoted, multi-source staged
    assert (populated_vault / "shared" / "project" / "agent-b__china-portal.md").exists()
    assert (populated_vault / "shared" / "feedback" / "use-yarn.md").exists()  # single-source → sacred
    assert (populated_vault / "shared" / "_inbox" / "feedback" / "no-commits-without-permission.md").exists()  # multi → inbox
    assert summary.auto_promoted == 1

    # Verify state
    state_file = populated_vault / ".mnemo" / "extraction-state.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    assert state["schema_version"] == 2
    assert any(k.startswith("project/") for k in state["entries"])
    assert any(k.startswith("feedback/") for k in state["entries"])


def test_second_run_unchanged_is_zero_calls(populated_vault, stub_llm_integration):
    response = _resp([
        {"slug": "use-yarn", "name": "Use yarn", "description": "d", "type": "feedback",
         "body": "b", "source_files": ["bots/agent-a/memory/feedback_use_yarn.md"]},
        {"slug": "no-commits-without-permission", "name": "NC", "description": "d", "type": "feedback",
         "body": "b", "source_files": ["bots/agent-b/memory/feedback_no_commits.md"]},
    ])
    stub_llm_integration([response])

    cfg = _cfg(populated_vault)
    run_extraction(cfg)
    summary2 = run_extraction(cfg)
    assert summary2.llm_calls == 0
    assert summary2.pages_written == 0
    assert summary2.unchanged_skipped > 0


def test_partial_failure_leaves_successful_work_on_disk(populated_vault, stub_llm_integration):
    stub_llm_integration([llm_mod.LLMSubprocessError("simulated failure")])
    summary = run_extraction(_cfg(populated_vault))

    assert summary.failed_chunks == 1
    assert summary.projects_promoted >= 1  # Projects wrote successfully
    state_file = populated_vault / ".mnemo" / "extraction-state.json"
    state = json.loads(state_file.read_text())
    assert any(k.startswith("project/") for k in state["entries"])
    # No feedback entries (the LLM call failed)
    assert not any(k.startswith("feedback/") for k in state["entries"])


def test_dry_run_zero_writes(populated_vault, stub_llm_integration):
    stub_llm_integration([])
    summary = run_extraction(_cfg(populated_vault), dry_run=True)
    assert summary.llm_calls == 0
    assert summary.pages_written == 0
    assert not (populated_vault / "shared" / "_inbox").exists()
    assert not (populated_vault / "shared" / "project").exists()
    assert not (populated_vault / ".mnemo" / "extraction-state.json").exists()


def test_extraction_rebuilds_rule_activation_index(populated_vault, stub_llm_integration):
    """Full pipeline: LLM emits enforce + activates_on, the extract pipeline
    persists them into frontmatter, and the rule_activation index on disk
    reflects both.
    """
    from mnemo.core import rule_activation

    stub_llm_integration([
        _resp([
            # Single-source page → auto-promoted to shared/feedback/ so it
            # passes is_consumer_visible and ends up in the index.
            {
                "slug": "no-coauthored",
                "name": "No Co-Authored-By trailers",
                "description": "never add Co-Authored-By trailers",
                "type": "feedback",
                "body": "rule body",
                "source_files": [
                    "bots/agent-a/memory/feedback_use_yarn.md",
                ],
                "stability": "stable",
                "tags": ["git"],
                "enforce": {
                    "tool": "Bash",
                    "deny_pattern": "git commit.*Co-Authored-By",
                    "reason": "No Co-Authored-By trailers in commits",
                },
            },
            {
                "slug": "heroui-drawer",
                "name": "HeroUI Drawer modal pattern",
                "description": "use Drawer for modals",
                "type": "feedback",
                "body": "HeroUI v3 drawer pattern",
                "source_files": [
                    "bots/agent-b/memory/feedback_no_commits.md",
                ],
                "stability": "stable",
                "tags": ["heroui"],
                "activates_on": {
                    "tools": ["Edit", "Write", "MultiEdit"],
                    "path_globs": [
                        "**/components/modals/**",
                        "**/*modal*.tsx",
                    ],
                },
            },
        ]),
    ])

    summary = run_extraction(_cfg(populated_vault))
    assert summary.failed_chunks == 0

    index_path = populated_vault / ".mnemo" / "rule-activation-index.json"
    assert index_path.exists(), "extraction must rebuild the rule-activation index"

    index = rule_activation.load_index(populated_vault)
    assert index is not None, "index must load cleanly"
    assert index.get("malformed") == []

    # C3 safety rail (2026-04-23): auto-promoted pages have enforce stripped.
    # The rule lands in the index with enforce=None; the file on disk carries
    # promoted_without_enforce: true so a human can re-add the block manually.
    no_coauthored = next(
        (rule for rule in index.get("rules", {}).values()
         if "agent-a" in rule.get("projects", [])),
        None,
    )
    assert no_coauthored is not None, f"no agent-a rule in index: {index.get('rules')}"
    assert no_coauthored.get("enforce") is None, (
        "auto-promoted rule must have enforce=None in index (C3 safety rail)"
    )
    # Confirm the disk file is flagged for review.
    target_path = next(
        (populated_vault / "shared" / "feedback" / f"{slug}.md"
         for slug in ["no-coauthored"]),
        None,
    )
    assert target_path.exists(), "promoted rule file must exist on disk"
    assert "promoted_without_enforce: true" in target_path.read_text(), (
        "promoted file must carry promoted_without_enforce: true frontmatter key"
    )

    rules_with_enrich = {
        slug: rule for slug, rule in index.get("rules", {}).items()
        if rule.get("activates_on") and "agent-b" in rule.get("projects", [])
    }
    assert rules_with_enrich, f"no enrich rules for agent-b: {index.get('rules')}"
    enrich_entries = [rule["activates_on"] for rule in rules_with_enrich.values()]
    assert any(
        "Edit" in r.get("tools", [])
        and "**/components/modals/**" in r.get("path_globs", [])
        and "**/*modal*.tsx" in r.get("path_globs", [])
        for r in enrich_entries
    ), enrich_entries


def test_conflict_flow_sibling_bounced(populated_vault, stub_llm_integration):
    # Single-source page is auto-promoted to shared/feedback/; when user edits
    # the sacred file and source changes, v0.3 writes a .proposed.md sibling
    # INTO _inbox/ rather than next to the sacred file.
    r1 = _resp([
        {"slug": "use-yarn", "name": "Use yarn", "description": "d", "type": "feedback",
         "body": "v1", "source_files": ["bots/agent-a/memory/feedback_use_yarn.md"]},
    ])
    r2 = _resp([
        {"slug": "use-yarn", "name": "Use yarn", "description": "d", "type": "feedback",
         "body": "v2 upstream", "source_files": ["bots/agent-a/memory/feedback_use_yarn.md"]},
    ])
    stub_llm_integration([r1, r2])

    cfg = _cfg(populated_vault)
    run_extraction(cfg)

    # Sacred file exists — user hand-edits it
    sacred = populated_vault / "shared" / "feedback" / "use-yarn.md"
    assert sacred.exists()
    sacred.write_text(sacred.read_text() + "\n\n(user note)\n")

    # Source changes — mutate the memory file
    yarn_mem = populated_vault / "bots" / "agent-a" / "memory" / "feedback_use_yarn.md"
    yarn_mem.write_text(yarn_mem.read_text() + "\n\nextra content\n")

    summary = run_extraction(cfg)

    sibling = populated_vault / "shared" / "_inbox" / "feedback" / "use-yarn.proposed.md"
    assert sibling.exists()
    assert "(user note)" in sacred.read_text(), "sacred file must be untouched"
    assert summary.sibling_bounced == 1
