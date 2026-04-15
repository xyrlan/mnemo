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
    # single-source yarn page auto-promoted to sacred dir
    assert (populated_vault / "shared" / "feedback" / "use-yarn.md").exists()
    # multi-source no-commits page staged in _inbox/
    assert (populated_vault / "shared" / "_inbox" / "feedback" / "no-commits-without-permission.md").exists()
    assert summary.auto_promoted == 1


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
    # First run writes a page, then we delete it, then force-run.
    # Use 2 sources to route through the _inbox/ branch (the original v0.2
    # test intent is about dismissed/force behavior of the inbox branch).
    response = _fake_llm_response([
        {"slug": "use-yarn", "name": "Use yarn", "description": "d", "type": "feedback",
         "body": "b",
         "source_files": [
             "bots/agent-a/memory/feedback_use_yarn.md",
             "bots/agent-a/memory/feedback_yarn_only.md",
         ]},
    ])
    stub_llm([response, response])
    cfg = _make_cfg(populated_vault)
    run_extraction(cfg)
    target = populated_vault / "shared" / "_inbox" / "feedback" / "use-yarn.md"
    target.unlink()

    run_extraction(cfg, force=True)
    assert target.exists()


def test_orchestrator_forwards_stability_from_llm_to_frontmatter(populated_vault: Path, stub_llm):
    """v0.3.1: when the LLM emits stability=evolving, the written page keeps it."""
    stub_llm([
        _fake_llm_response([
            {
                "slug": "use-yarn",
                "name": "Use yarn",
                "description": "d",
                "type": "feedback",
                "body": "yarn body",
                "source_files": ["bots/agent-a/memory/feedback_use_yarn.md"],
                "stability": "evolving",
            },
        ]),
    ])

    run_extraction(_make_cfg(populated_vault))

    sacred = populated_vault / "shared" / "feedback" / "use-yarn.md"
    assert sacred.exists()
    assert "stability: evolving" in sacred.read_text()


def test_orchestrator_defaults_stability_to_stable_when_llm_omits_field(populated_vault: Path, stub_llm):
    """v0.3.1: LLM responses without a stability key default to 'stable' — forward compat."""
    stub_llm([
        _fake_llm_response([
            {
                "slug": "use-yarn",
                "name": "Use yarn",
                "description": "d",
                "type": "feedback",
                "body": "yarn body",
                "source_files": ["bots/agent-a/memory/feedback_use_yarn.md"],
                # no "stability" field — legacy v0.2/v0.3 schema
            },
        ]),
    ])

    run_extraction(_make_cfg(populated_vault))

    sacred = populated_vault / "shared" / "feedback" / "use-yarn.md"
    assert sacred.exists()
    assert "stability: stable" in sacred.read_text()


def test_orchestrator_force_wipes_inbox_type_dirs_before_run(populated_vault: Path, stub_llm):
    """v0.3.1: --force nukes shared/_inbox/<type>/*.md so slug-drift duplicates die."""
    # Seed the inbox with stale slug-drift duplicates from a prior run.
    feedback_inbox = populated_vault / "shared" / "_inbox" / "feedback"
    feedback_inbox.mkdir(parents=True, exist_ok=True)
    (feedback_inbox / "no-commits-only-edits.md").write_text("stale body 1\n")
    (feedback_inbox / "no-commits-without-permission.md").write_text("stale body 2\n")
    (feedback_inbox / "no-git-commits-without-permission.md").write_text("stale body 3\n")

    stub_llm([
        _fake_llm_response([
            {
                "slug": "no-commits",
                "name": "No commits",
                "description": "d",
                "type": "feedback",
                "body": "canonical body",
                "source_files": [
                    "bots/agent-b/memory/feedback_no_commits.md",
                    "bots/agent-b/memory/feedback_no_commit_without_permission.md",
                ],
            },
        ]),
    ])

    run_extraction(_make_cfg(populated_vault), force=True)

    # Old slug-drift duplicates must be gone.
    assert not (feedback_inbox / "no-commits-only-edits.md").exists()
    assert not (feedback_inbox / "no-commits-without-permission.md").exists()
    assert not (feedback_inbox / "no-git-commits-without-permission.md").exists()
    # The freshly extracted canonical slug is present.
    assert (feedback_inbox / "no-commits.md").exists()


def test_orchestrator_non_force_preserves_inbox_files(populated_vault: Path, stub_llm):
    """Sanity: without --force, existing inbox files are preserved."""
    feedback_inbox = populated_vault / "shared" / "_inbox" / "feedback"
    feedback_inbox.mkdir(parents=True, exist_ok=True)
    preserved = feedback_inbox / "preserved.md"
    preserved.write_text("keep me\n")

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
        ]),
    ])

    run_extraction(_make_cfg(populated_vault))

    assert preserved.exists(), "non-force runs must not wipe inbox"


def test_orchestrator_force_wipes_only_cluster_type_dirs(populated_vault: Path, stub_llm):
    """Force wipes feedback/user/reference under _inbox, not project/ or random subdirs."""
    project_inbox = populated_vault / "shared" / "_inbox" / "project"
    project_inbox.mkdir(parents=True, exist_ok=True)
    project_stale = project_inbox / "some-project.md"
    project_stale.write_text("project body\n")

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
        ]),
    ])

    run_extraction(_make_cfg(populated_vault), force=True)

    # Project inbox untouched (projects don't LLM-cluster, no slug drift)
    assert project_stale.exists()


def test_summary_has_v0_3_fields():
    from mnemo.core.extract import ExtractionSummary

    summary = ExtractionSummary()
    assert summary.auto_promoted == 0
    assert summary.sibling_bounced == 0
    assert summary.upgrade_proposed == 0
    assert summary.mode == "manual"


def test_merge_apply_folds_new_fields():
    from mnemo.core.extract import _merge_apply, ExtractionSummary
    from mnemo.core.extract.inbox import ApplyResult

    apply_result = ApplyResult()
    apply_result.auto_promoted = ["feedback/use-yarn", "feedback/no-bun"]
    apply_result.sibling_bounced = [("feedback/use-yarn", "path/to/proposed.md")]
    apply_result.upgrade_proposed = [("feedback/no-commits", "path/to/upgrade.md")]

    summary = ExtractionSummary()
    _merge_apply(apply_result, summary)

    assert summary.auto_promoted == 2
    assert summary.sibling_bounced == 1
    assert summary.upgrade_proposed == 1
    # Existing v0.2 semantics: auto-promoted pages also count toward pages_written
    assert summary.pages_written == 2


def test_run_extraction_background_writes_last_auto_run_json_on_success(tmp_path):
    from mnemo.core.extract import run_extraction

    vault = tmp_path / "vault"
    (vault / "bots").mkdir(parents=True)
    (vault / ".mnemo").mkdir()

    cfg = {
        "vaultRoot": str(vault),
        "extraction": {
            "model": "claude-haiku-4-5",
            "chunkSize": 10,
            "subprocessTimeout": 60,
        },
    }

    # Empty scan → no LLM calls
    summary = run_extraction(cfg, dry_run=False, force=False, background=True)

    last_run_path = vault / ".mnemo" / "last-auto-run.json"
    assert last_run_path.exists(), "last-auto-run.json must be written in background mode"

    payload = json.loads(last_run_path.read_text())
    assert payload["mode"] == "background"
    assert payload["exit_code"] == 0
    assert payload["error"] is None
    assert "summary" in payload
    assert summary.mode == "background"


def test_run_extraction_manual_does_not_write_last_auto_run_json(tmp_path):
    from mnemo.core.extract import run_extraction

    vault = tmp_path / "vault"
    (vault / "bots").mkdir(parents=True)
    (vault / ".mnemo").mkdir()

    cfg = {
        "vaultRoot": str(vault),
        "extraction": {
            "model": "claude-haiku-4-5",
            "chunkSize": 10,
            "subprocessTimeout": 60,
        },
    }

    run_extraction(cfg, dry_run=False, force=False, background=False)

    last_run_path = vault / ".mnemo" / "last-auto-run.json"
    assert not last_run_path.exists(), "manual runs must not write last-auto-run.json"
