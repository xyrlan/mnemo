"""Unit tests for core/extract orchestrator (run_extraction)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core import llm as llm_mod
from mnemo.core.extract import run_extraction


def _make_cfg(vault_root: Path) -> dict:
    return {
        "vaultRoot": str(vault_root),
        "extraction": {
            "model": "claude-haiku-4-5",
            "chunkSize": 10,
            "hintThreshold": 5,
            "preferAPI": False,
            "subprocessTimeout": 60,
            "costSoftCap": None,
        },
    }


def _fake_llm_response(pages: list[dict]) -> llm_mod.LLMResponse:
    text = json.dumps({"pages": pages})
    return llm_mod.LLMResponse(
        text=text,
        total_cost_usd=0.0048,
        input_tokens=500,
        output_tokens=200,
        api_key_source="none",
        raw={"result": text},
    )


@pytest.fixture
def stub_llm(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []
    responses: list = []

    def installer(queue: list):
        responses.extend(queue)

    def fake_call(prompt, *, system, model, timeout):
        calls.append({"prompt": prompt, "system": system, "model": model})
        if not responses:
            raise AssertionError("stub_llm: queue exhausted")
        item = responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(llm_mod, "call", fake_call)
    installer.calls = calls  # type: ignore[attr-defined]
    return installer


def test_orchestrator_first_run_writes_projects_and_clusters(populated_vault: Path, stub_llm):
    stub_llm([
        _fake_llm_response([
            {
                "slug": "use-yarn",
                "name": "Use yarn",
                "description": "d",
                "type": "feedback",
                "body": "yarn body",
                "source_files": ["bots/agent-a/memory/feedback_use_yarn.md"],
            },
            {
                "slug": "no-commits-without-permission",
                "name": "No commits",
                "description": "d",
                "type": "feedback",
                "body": "no commits body",
                "source_files": [
                    "bots/agent-b/memory/feedback_no_commits.md",
                    "bots/agent-b/memory/feedback_no_commit_without_permission.md",
                ],
            },
        ]),
    ])

    summary = run_extraction(_make_cfg(populated_vault))

    assert summary.projects_promoted == 1
    assert summary.pages_written >= 2
    assert summary.llm_calls == 1
    assert summary.failed_chunks == 0
    assert summary.all_calls_subscription is True
    # project file exists
    assert (populated_vault / "shared" / "project" / "agent-b__china-portal.md").exists()
    # inbox files exist
    assert (populated_vault / "shared" / "_inbox" / "feedback" / "use-yarn.md").exists()


def test_orchestrator_second_run_skips_unchanged(populated_vault: Path, stub_llm):
    response = _fake_llm_response([
        {"slug": "use-yarn", "name": "Use yarn", "description": "d", "type": "feedback",
         "body": "b", "source_files": ["bots/agent-a/memory/feedback_use_yarn.md"]},
    ])
    stub_llm([response, response])

    cfg = _make_cfg(populated_vault)
    run_extraction(cfg)
    summary = run_extraction(cfg)
    # Second run: scanner finds nothing dirty -> zero LLM calls
    assert summary.llm_calls == 0
    assert summary.pages_written == 0
    assert summary.unchanged_skipped > 0


def test_orchestrator_dry_run_makes_no_calls(populated_vault: Path, stub_llm):
    stub_llm([])  # no responses needed
    summary = run_extraction(_make_cfg(populated_vault), dry_run=True)
    assert summary.llm_calls == 0
    assert summary.pages_written == 0
    # No files on disk
    assert not (populated_vault / "shared" / "_inbox").exists()


def test_orchestrator_partial_failure_records_and_continues(populated_vault: Path, stub_llm):
    stub_llm([llm_mod.LLMSubprocessError("simulated timeout")])
    summary = run_extraction(_make_cfg(populated_vault))
    assert summary.failed_chunks == 1
    # Projects still promoted (they run before LLM)
    assert summary.projects_promoted == 1


def test_orchestrator_flushes_state_after_each_phase(populated_vault: Path, stub_llm):
    stub_llm([llm_mod.LLMSubprocessError("boom")])
    run_extraction(_make_cfg(populated_vault))
    state_file = populated_vault / ".mnemo" / "extraction-state.json"
    assert state_file.exists()
    payload = json.loads(state_file.read_text())
    # Project entries were flushed even though LLM phase blew up
    assert any(k.startswith("project/") for k in payload["entries"])


def test_orchestrator_aborts_when_lock_held(populated_vault: Path, stub_llm):
    lock_dir = populated_vault / ".mnemo" / "extract.lock"
    lock_dir.mkdir(parents=True)
    from mnemo.core.extract import ExtractionIOError
    with pytest.raises(ExtractionIOError, match="in progress"):
        run_extraction(_make_cfg(populated_vault))


def test_orchestrator_force_reprocesses_dismissed(populated_vault: Path, stub_llm):
    # First run writes an inbox file, then we delete it, then force-run.
    response = _fake_llm_response([
        {"slug": "use-yarn", "name": "Use yarn", "description": "d", "type": "feedback",
         "body": "b", "source_files": ["bots/agent-a/memory/feedback_use_yarn.md"]},
    ])
    stub_llm([response, response])
    cfg = _make_cfg(populated_vault)
    run_extraction(cfg)
    target = populated_vault / "shared" / "_inbox" / "feedback" / "use-yarn.md"
    target.unlink()

    run_extraction(cfg, force=True)
    assert target.exists()
