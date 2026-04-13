"""Opt-in end-to-end smoke test with a real `claude` subprocess.

Skipped unless MNEMO_E2E=1 is set AND the `claude` CLI is on PATH. This
test makes one real LLM call and costs ~$0.005 with API keys, $0 with
subscription.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MNEMO_E2E") != "1" or shutil.which("claude") is None,
    reason="opt-in; set MNEMO_E2E=1 and have claude CLI on PATH",
)


def test_real_llm_extraction_roundtrip(tmp_vault, tmp_home, memory_fixture):
    import json
    from mnemo.core.extract import run_extraction

    # Populate with 2 trivial feedback files
    agent_dir = tmp_vault / "bots" / "smoke-agent" / "memory"
    agent_dir.mkdir(parents=True)
    shutil.copy(memory_fixture / "feedback_use_yarn.md", agent_dir / "feedback_use_yarn.md")
    shutil.copy(memory_fixture / "feedback_no_commits.md", agent_dir / "feedback_no_commits.md")

    cfg = {
        "vaultRoot": str(tmp_vault),
        "extraction": {
            "model": "claude-haiku-4-5",
            "chunkSize": 10,
            "hintThreshold": 5,
            "preferAPI": False,
            "subprocessTimeout": 120,
            "costSoftCap": None,
        },
    }
    (tmp_vault / "mnemo.config.json").write_text(json.dumps(cfg))

    summary = run_extraction(cfg)

    assert summary.failed_chunks == 0
    assert summary.llm_calls >= 1
    assert summary.pages_written >= 1

    inbox_dir = tmp_vault / "shared" / "_inbox" / "feedback"
    assert inbox_dir.exists()
    produced = list(inbox_dir.glob("*.md"))
    assert len(produced) >= 1

    # Spot-check content: frontmatter + non-empty body
    sample = produced[0].read_text()
    assert "---" in sample
    assert "needs-review" in sample
    assert len(sample.strip()) > 100

    # Subscription contract (if user is on subscription)
    total_tokens = summary.total_input_tokens + summary.total_output_tokens
    assert summary.all_calls_subscription or total_tokens > 0


def test_real_llm_strips_plugin_tools_and_thinking():
    """Issues #6 + #7 acceptance on a real subprocess call.

    #6: `--strict-mcp-config` + `--tools ""` must strip both built-in and
        MCP plugin tools from the init event, regardless of what the user
        has configured in their Claude Code settings.
    #7: `CLAUDE_CODE_DISABLE_THINKING=1` must suppress the extended-thinking
        block on Haiku 4.5, keeping wall-time and output-tokens low on
        trivial prompts.
    """
    from mnemo.core import llm

    resp = llm.call(
        "Reply with the exact JSON: {}",
        system="You are a JSON-only tool. Respond with `{}` and nothing else.",
        model="claude-haiku-4-5",
        timeout=60,
    )

    # --- Issue #6 acceptance ------------------------------------------------
    init = resp.raw.get("init") or {}
    assert init.get("tools") == [], (
        f"init.tools must be empty, got {init.get('tools')!r}"
    )
    assert init.get("mcp_servers") == [], (
        f"init.mcp_servers must be empty, got {init.get('mcp_servers')!r}"
    )

    # --- Issue #7 acceptance ------------------------------------------------
    events = resp.raw.get("events") or []
    thinking_blocks = [
        c
        for ev in events
        if isinstance(ev, dict) and ev.get("type") == "assistant"
        for c in (ev.get("message") or {}).get("content", [])
        if isinstance(c, dict) and c.get("type") == "thinking"
    ]
    assert not thinking_blocks, (
        f"CLAUDE_CODE_DISABLE_THINKING=1 should suppress thinking blocks, "
        f"but found {len(thinking_blocks)} on this response"
    )

    result_event = resp.raw.get("result") or {}
    duration_ms = result_event.get("duration_ms")
    assert duration_ms is not None and duration_ms < 15_000, (
        f"trivial prompt should finish in <15s wall-time, got {duration_ms}ms"
    )
    # Output tokens should be tiny for a trivial JSON reply (was ~300-500
    # with thinking enabled, should be <50 without)
    assert resp.output_tokens is not None and resp.output_tokens < 50, (
        f"trivial prompt output should be <50 tokens, got {resp.output_tokens}"
    )
