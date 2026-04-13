"""v0.3 full-pipeline integration: mocked LLM + real filesystem."""
from __future__ import annotations

import json


def _write_memory(vault, agent, stem, type_, content_suffix=""):
    path = vault / "bots" / agent / "memory" / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"name: {stem}\n"
        f"description: test\n"
        f"type: {type_}\n"
        "---\n\n"
        f"body content for {stem}{content_suffix}\n"
    )


def _mock_llm_response(pages):
    """Return a fake LLMResponse-compatible object."""
    from mnemo.core.llm import LLMResponse

    payload = json.dumps({"pages": pages})
    return LLMResponse(
        text=payload,
        total_cost_usd=0.0,
        input_tokens=100,
        output_tokens=50,
        api_key_source="none",
        raw={},
    )


def test_first_auto_run_splits_single_and_multi_source(tmp_path, monkeypatch):
    from mnemo.core import llm
    from mnemo.core import extract as extract_mod

    vault = tmp_path / "vault"
    _write_memory(vault, "clubinho", "feedback_use_yarn", "feedback")
    _write_memory(vault, "central", "feedback_no_commits", "feedback")
    _write_memory(vault, "clubinho", "feedback_no_commit_without_permission", "feedback")

    def fake_call(prompt, *, system, model, timeout):
        return _mock_llm_response([
            {
                "slug": "use-yarn",
                "type": "feedback",
                "name": "Use yarn",
                "description": "",
                "body": "Always use yarn.",
                "source_files": ["bots/clubinho/memory/feedback_use_yarn.md"],
            },
            {
                "slug": "no-commits",
                "type": "feedback",
                "name": "No commits without permission",
                "description": "",
                "body": "Do not commit without permission.",
                "source_files": [
                    "bots/central/memory/feedback_no_commits.md",
                    "bots/clubinho/memory/feedback_no_commit_without_permission.md",
                ],
            },
        ])
    monkeypatch.setattr(llm, "call", fake_call)

    cfg = {
        "vaultRoot": str(vault),
        "extraction": {
            "model": "claude-haiku-4-5",
            "chunkSize": 10,
            "subprocessTimeout": 60,
        },
    }

    summary = extract_mod.run_extraction(cfg, background=True)

    single_target = vault / "shared" / "feedback" / "use-yarn.md"
    assert single_target.exists()
    single_content = single_target.read_text()
    assert "auto-promoted" in single_content
    assert "last_sync:" in single_content

    multi_target = vault / "shared" / "_inbox" / "feedback" / "no-commits.md"
    assert multi_target.exists()
    multi_content = multi_target.read_text()
    assert "needs-review" in multi_content
    assert "auto-promoted" not in multi_content

    assert summary.auto_promoted == 1
    assert summary.pages_written == 2
    assert summary.mode == "background"

    last_run = vault / ".mnemo" / "last-auto-run.json"
    assert last_run.exists()
    payload = json.loads(last_run.read_text())
    assert payload["mode"] == "background"
    assert payload["exit_code"] == 0
    assert payload["summary"]["auto_promoted"] == 1


def test_second_run_unchanged_source_is_noop(tmp_path, monkeypatch):
    from mnemo.core import llm
    from mnemo.core import extract as extract_mod

    vault = tmp_path / "vault"
    _write_memory(vault, "clubinho", "feedback_use_yarn", "feedback")

    call_count = {"n": 0}

    def fake_call(prompt, *, system, model, timeout):
        call_count["n"] += 1
        return _mock_llm_response([
            {
                "slug": "use-yarn",
                "type": "feedback",
                "name": "Use yarn",
                "description": "",
                "body": "Always use yarn.",
                "source_files": ["bots/clubinho/memory/feedback_use_yarn.md"],
            },
        ])
    monkeypatch.setattr(llm, "call", fake_call)

    cfg = {
        "vaultRoot": str(vault),
        "extraction": {"model": "claude-haiku-4-5", "chunkSize": 10, "subprocessTimeout": 60},
    }

    extract_mod.run_extraction(cfg, background=True)
    extract_mod.run_extraction(cfg, background=True)

    assert call_count["n"] == 1, "second run with unchanged source should skip LLM call"


def test_user_edit_on_sacred_produces_bounced_sibling(tmp_path, monkeypatch):
    from mnemo.core import llm
    from mnemo.core import extract as extract_mod

    vault = tmp_path / "vault"
    _write_memory(vault, "clubinho", "feedback_use_yarn", "feedback")

    def fake_call_v1(prompt, *, system, model, timeout):
        return _mock_llm_response([
            {
                "slug": "use-yarn",
                "type": "feedback",
                "name": "Use yarn",
                "description": "",
                "body": "Always use yarn.",
                "source_files": ["bots/clubinho/memory/feedback_use_yarn.md"],
            },
        ])
    monkeypatch.setattr(llm, "call", fake_call_v1)
    cfg = {
        "vaultRoot": str(vault),
        "extraction": {"model": "claude-haiku-4-5", "chunkSize": 10, "subprocessTimeout": 60},
    }
    extract_mod.run_extraction(cfg, background=True)

    sacred = vault / "shared" / "feedback" / "use-yarn.md"
    sacred.write_text(sacred.read_text() + "\n\n(User addition)\n")

    _write_memory(vault, "clubinho", "feedback_use_yarn", "feedback", content_suffix=" (updated)")

    def fake_call_v2(prompt, *, system, model, timeout):
        return _mock_llm_response([
            {
                "slug": "use-yarn",
                "type": "feedback",
                "name": "Use yarn",
                "description": "",
                "body": "Always use yarn. Updated.",
                "source_files": ["bots/clubinho/memory/feedback_use_yarn.md"],
            },
        ])
    monkeypatch.setattr(llm, "call", fake_call_v2)

    summary = extract_mod.run_extraction(cfg, background=True)

    sibling = vault / "shared" / "_inbox" / "feedback" / "use-yarn.proposed.md"
    assert sibling.exists()
    assert "Updated" in sibling.read_text()
    assert "(User addition)" in sacred.read_text()
    assert summary.sibling_bounced == 1
