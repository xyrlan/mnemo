"""Unit tests for core/extract/guards — the prompt-echo rejection gate.

Plus an end-to-end proof that ``_run_extraction_body`` runs both the echo
guard and the PII redactor between parsing and apply.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core import llm as llm_mod
from mnemo.core.extract import run_extraction
from mnemo.core.extract.guards import is_prompt_echo
from mnemo.core.extract.inbox.types import ExtractedPage


def _p(name="N", description="D", body="B"):
    return ExtractedPage(slug="s", type="feedback", name=name, description=description, body=body,
                         source_files=["a.md"], source_hash="h")


def test_rejects_pages_that_echo_extractor_guidance():
    assert is_prompt_echo(_p(body="Only emit enforce blocks when the rule has explicit blocking intent."))
    assert is_prompt_echo(_p(name="Stability field must be stable or evolving"))
    assert is_prompt_echo(_p(description="Prefer Existing vault tags over inventing new ones"))


def test_accepts_ordinary_rules():
    assert not is_prompt_echo(_p(body="Never retry on 4xx.\n\n**Why:** x\n\n**How to apply:** y"))


# --- end-to-end: guard + redactor inside _run_extraction_body ---------------


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


def test_orchestrator_drops_prompt_echo_and_redacts_pii(
    populated_vault: Path, stub_llm,
):
    """Two feedback pages off one briefing: the echo is written nowhere, and
    the clean verified page reaches the sacred dir with its e-mail redacted."""
    briefing = populated_vault / "bots" / "proj" / "briefings" / "sessions" / "s1.md"
    briefing.parent.mkdir(parents=True)
    briefing.write_text(
        "---\n"
        "type: briefing\n"
        "agent: proj\n"
        "session_id: s1\n"
        "---\n"
        "\n"
        "# Briefing — proj — s1\n"
        "\n"
        "## Corrections\n"
        '- "never retry on 4xx, only on 5xx" → Retry only on 5xx\n',
        encoding="utf-8",
    )
    src = "bots/proj/briefings/sessions/s1.md"
    stub_llm([
        _fake_llm_response([
            {
                "slug": "enforce-block-guidance",
                "name": "Emit enforce blocks",
                "description": "d",
                "type": "feedback",
                "body": "Only emit an enforce block when the rule blocks a tool.",
                "source_files": [src],
                "evidence": {"quote": "never retry on 4xx, only on 5xx", "source": src},
            },
            {
                "slug": "retry-5xx-only",
                "name": "Retry only on 5xx",
                "description": "d",
                "type": "feedback",
                "body": "Retry only 5xx. Ask ana.silva@example.com before changing.",
                "source_files": [src],
                "evidence": {"quote": "never retry on 4xx, only on 5xx", "source": src},
            },
        ]),
    ])

    summary = run_extraction(_make_cfg(populated_vault))

    assert summary.echo_rejected == 1
    assert summary.redactions == 1

    # The echo page is written nowhere at all.
    assert not (populated_vault / "shared" / "feedback" / "enforce-block-guidance.md").exists()
    assert not (populated_vault / "shared" / "_inbox" / "feedback" / "enforce-block-guidance.md").exists()
    assert not (populated_vault / "shared" / "_inbox" / "reference" / "enforce-block-guidance.md").exists()

    promoted = populated_vault / "shared" / "feedback" / "retry-5xx-only.md"
    assert promoted.exists(), "a verified quote must reach the sacred dir"
    text = promoted.read_text(encoding="utf-8")
    assert "[redacted]" in text
    assert "ana.silva@example.com" not in text
