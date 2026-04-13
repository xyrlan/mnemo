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
