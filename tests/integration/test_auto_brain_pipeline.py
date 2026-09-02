"""v0.3 full-pipeline integration: mocked LLM + real filesystem."""
from __future__ import annotations

import json


# A feedback page only reaches shared/feedback/ when its ``evidence`` quotes the
# ``## Corrections`` section of a briefing it was actually built from (the
# evidence gate, src/mnemo/core/extract/evidence.py). These tests are about the
# routing that happens *after* that gate, so their pages are made verifiable:
# a briefing on disk, cited as the page's source, quoted verbatim.
_QUOTES = {
    "clubinho": "always use yarn, never npm install",
    "central": "never commit without asking me first",
}


def _briefing_path(agent: str) -> str:
    return f"bots/{agent}/briefings/sessions/s1.md"


def _write_briefing(vault, agent, rule="Follow the correction"):
    path = vault / "bots" / agent / "briefings" / "sessions" / "s1.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "type: briefing\n"
        f"agent: {agent}\n"
        "session_id: s1\n"
        "---\n\n"
        f"# Briefing — {agent} — s1\n\n"
        "## Corrections\n"
        f'- "{_QUOTES[agent]}" → {rule}\n',
        encoding="utf-8",
    )
    return path


def _evidence(agent: str) -> dict:
    return {"quote": _QUOTES[agent], "source": _briefing_path(agent)}


def _write_memory(vault, agent, stem, type_, content_suffix=""):
    path = vault / "bots" / agent / "memory" / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"name: {stem}\n"
        f"description: test\n"
        f"type: {type_}\n"
        "---\n\n"
        f"body content for {stem}{content_suffix}\n", 
    encoding="utf-8")


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
    _write_briefing(vault, "clubinho", rule="Use yarn")
    _write_briefing(vault, "central", rule="Ask before committing")

    def fake_call(prompt, *, system, model, timeout):
        return _mock_llm_response([
            {
                "slug": "use-yarn",
                "type": "feedback",
                "name": "Use yarn",
                "description": "",
                "body": "Always use yarn.",
                # One source, so the single-source auto-promote door; the
                # source is the briefing the evidence quote comes from.
                "source_files": [_briefing_path("clubinho")],
                "evidence": _evidence("clubinho"),
            },
            {
                "slug": "no-commits",
                "type": "feedback",
                "name": "No commits without permission",
                "description": "",
                "body": "Do not commit without permission.",
                "source_files": [
                    _briefing_path("central"),
                    "bots/clubinho/memory/feedback_no_commit_without_permission.md",
                ],
                "evidence": _evidence("central"),
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
    single_content = single_target.read_text(encoding="utf-8")
    assert "auto-promoted" in single_content
    assert "last_sync:" in single_content

    # Multi-source page spans two distinct projects (clubinho + central), so
    # it crosses universalThreshold=2 and is intercepted by the
    # universal-promotion dispatch row — landing in shared/<type>/ directly
    # instead of staging under _inbox/.
    multi_target = vault / "shared" / "feedback" / "no-commits.md"
    assert multi_target.exists()
    assert not (vault / "shared" / "_inbox" / "feedback" / "no-commits.md").exists()
    multi_content = multi_target.read_text(encoding="utf-8")
    assert "auto-promoted" in multi_content

    assert summary.auto_promoted == 1
    assert summary.universal_promoted == 1
    assert summary.pages_written == 2
    assert summary.mode == "background"

    last_run = vault / ".mnemo" / "last-auto-run.json"
    assert last_run.exists()
    payload = json.loads(last_run.read_text(encoding="utf-8"))
    assert payload["mode"] == "background"
    assert payload["exit_code"] == 0
    assert payload["summary"]["auto_promoted"] == 1
    assert payload["summary"]["universal_promoted"] == 1


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
    # The sibling bounce is only reachable once the page is in the sacred dir,
    # so the page has to clear the evidence gate: one briefing source, quoted.
    _write_briefing(vault, "clubinho", rule="Use yarn")

    def fake_call_v1(prompt, *, system, model, timeout):
        return _mock_llm_response([
            {
                "slug": "use-yarn",
                "type": "feedback",
                "name": "Use yarn",
                "description": "",
                "body": "Always use yarn.",
                "source_files": [_briefing_path("clubinho")],
                "evidence": _evidence("clubinho"),
            },
        ])
    monkeypatch.setattr(llm, "call", fake_call_v1)
    cfg = {
        "vaultRoot": str(vault),
        "extraction": {"model": "claude-haiku-4-5", "chunkSize": 10, "subprocessTimeout": 60},
    }
    extract_mod.run_extraction(cfg, background=True)

    sacred = vault / "shared" / "feedback" / "use-yarn.md"
    sacred.write_text(sacred.read_text(encoding="utf-8") + "\n\n(User addition)\n", encoding="utf-8")

    _write_memory(vault, "clubinho", "feedback_use_yarn", "feedback", content_suffix=" (updated)")

    def fake_call_v2(prompt, *, system, model, timeout):
        return _mock_llm_response([
            {
                "slug": "use-yarn",
                "type": "feedback",
                "name": "Use yarn",
                "description": "",
                "body": "Always use yarn. Updated.",
                "source_files": [_briefing_path("clubinho")],
                "evidence": _evidence("clubinho"),
            },
        ])
    monkeypatch.setattr(llm, "call", fake_call_v2)

    summary = extract_mod.run_extraction(cfg, background=True)

    sibling = vault / "shared" / "_inbox" / "feedback" / "use-yarn.proposed.md"
    assert sibling.exists()
    assert "Updated" in sibling.read_text(encoding="utf-8")
    assert "(User addition)" in sacred.read_text(encoding="utf-8")
    assert summary.sibling_bounced == 1
