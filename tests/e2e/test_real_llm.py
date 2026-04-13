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
