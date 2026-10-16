"""The session briefing reads a Grok history; a Claude transcript's briefing is unchanged."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core import briefing
from mnemo.core import llm as llm_mod
from mnemo.core.extract import prompts
from mnemo.core.grok import paths as grok_paths
from mnemo.core.transcript import flatten_transcript_events, user_turns
from tests.unit._grok_history import PROMPT, SESSION_ID, recorded_history, write_session

BODY = "## TL;DR\nWrote notes.txt.\n\n## What I did\n- wrote and edited notes.txt\n"


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: root)
    return root


class _Calls(list):
    """The prompts the stubbed model was sent; ``response`` is what it answers."""

    response = BODY


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> _Calls:
    seen = _Calls()

    def fake_call(prompt, *, system, model, timeout):
        seen.append(prompt)
        return llm_mod.LLMResponse(text=seen.response, total_cost_usd=0.0, input_tokens=1,
                                   output_tokens=1, api_key_source="none", raw={})

    monkeypatch.setattr(llm_mod, "call", fake_call)
    return seen


def test_a_grok_session_is_briefed_under_its_own_id(vault: Path, tmp_path: Path, calls):
    history = write_session(tmp_path / ".grok")

    out = briefing.generate_session_briefing(history, "proj", {"extraction": {}})

    assert out == vault / "bots" / "proj" / "briefings" / "sessions" / f"{SESSION_ID}.md"
    text = out.read_text(encoding="utf-8")
    assert f"session_id: {SESSION_ID}\n" in text
    assert "date: 2026-10-16\n" in text
    assert f"transcript_sha256: {briefing.transcript_sha256(history)}\n" in text
    assert "Wrote notes.txt." in text


def test_the_prompt_holds_the_session_and_not_what_grok_injected(vault: Path, tmp_path: Path, calls):
    briefing.generate_session_briefing(write_session(tmp_path / ".grok"), "proj", {"extraction": {}})

    prompt = calls[0]
    assert f"[user] {PROMPT}" in prompt
    assert "[tool_use: search_replace]" in prompt
    assert f"[1] {PROMPT}" in prompt  # the numbered user turns corrections are checked against
    assert "user_info" not in prompt and "skills are available" not in prompt
    assert "You are Grok" not in prompt


def test_the_hooks_transcript_path_briefs_the_same_session(vault: Path, tmp_path: Path, calls):
    history = write_session(tmp_path / ".grok")

    out = briefing.generate_session_briefing(
        history.with_name(grok_paths.UPDATES), "proj", {"extraction": {}},
    )

    assert out is not None and out.name == f"{SESSION_ID}.md"
    assert briefing.transcript_sha256(history.with_name(grok_paths.UPDATES)) == briefing.transcript_sha256(history)


def test_a_grok_session_without_edits_is_skipped_at_the_default_threshold(vault: Path, tmp_path: Path, calls):
    records = [r for r in recorded_history() if not (
        r["type"] == "assistant" and any(c["name"] in ("write", "search_replace") for c in r.get("tool_calls", []))
    )]
    history = write_session(tmp_path / ".grok", records=records)

    assert briefing.generate_session_briefing(history, "proj", {"extraction": {}}) is None
    assert calls == []
    assert briefing.generate_session_briefing(history, "proj", {"extraction": {}}, min_mutations=0) is not None


def test_a_correction_typed_in_grok_is_verified_against_the_users_words(
    vault: Path, tmp_path: Path, calls, monkeypatch,
):
    monkeypatch.setattr("mnemo.core.errors.log_error", lambda *a, **k: None)
    records = recorded_history() + [
        {"type": "user", "content": [{"type": "text", "text": (
            "<user_query>\nno — never write notes in the repo root\n</user_query>")}], "prompt_index": 1},
        {"type": "user", "content": [{"type": "text", "text": "<system-reminder>\nalways use tabs\n</system-reminder>"}],
         "synthetic_reason": "system_reminder"},
        {"type": "assistant", "content": "Understood."},
    ]
    history = write_session(tmp_path / ".grok", records=records)
    calls.response = (
        BODY + "\n## Corrections\n"
        '- "never write notes in the repo root" → Keep notes out of the repo root\n'
        '- "always use tabs" → Use tabs\n'
    )

    text = briefing.generate_session_briefing(history, "proj", {"extraction": {}}).read_text(encoding="utf-8")

    assert '"never write notes in the repo root" → Keep notes out of the repo root' in text
    assert "always use tabs" not in text  # Grok injected it; the person never typed it
    assert "corrections: 1\n" in text


# --- Claude stays byte-identical (spec decision 8) ---------------------------

CLAUDE_EVENTS = [
    {"type": "user", "timestamp": "2026-04-14T10:00:00.000Z", "cwd": "/Users/you/proj",
     "message": {"role": "user", "content": "add a retry helper"}},
    {"type": "assistant", "timestamp": "2026-04-14T10:05:00.000Z",
     "message": {"role": "assistant", "content": [
         {"type": "text", "text": "I'll add the helper."},
         {"type": "tool_use", "name": "Write", "input": {"file_path": "retry.py"}}]}},
    {"type": "user", "timestamp": "2026-04-14T10:06:00.000Z",
     "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
    {"type": "assistant", "timestamp": "2026-04-14T10:42:00.000Z",
     "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}},
]


def test_a_claude_transcript_is_briefed_exactly_as_before(vault: Path, tmp_path: Path, calls):
    jsonl = tmp_path / "projects" / "-Users-you-proj" / "abc123.jsonl"
    jsonl.parent.mkdir(parents=True)
    jsonl.write_text("\n".join(json.dumps(e) for e in CLAUDE_EVENTS) + "\n", encoding="utf-8")

    out = briefing.generate_session_briefing(jsonl, "proj", {"extraction": {}})

    assert briefing._load_jsonl_events(jsonl) == CLAUDE_EVENTS
    assert briefing.session_id_for(jsonl) == "abc123"
    assert calls == [prompts.build_briefing_prompt(
        flatten_transcript_events(CLAUDE_EVENTS), user_turns=user_turns(CLAUDE_EVENTS),
    )]
    assert out.read_text(encoding="utf-8") == (
        "---\n"
        "type: briefing\n"
        "agent: proj\n"
        "session_id: abc123\n"
        "date: 2026-04-14\n"
        "duration_minutes: 42\n"
        "corrections: 0\n"
        f"transcript_sha256: {briefing.transcript_sha256(jsonl)}\n"
        "---\n\n"
        "# Briefing — proj — abc123\n\n"
        f"{BODY.strip()}\n"
    )


def test_claude_mutation_names_are_unchanged_and_grok_names_only_add():
    assert {"Edit", "Write", "MultiEdit", "NotebookEdit"} <= briefing.MUTATION_TOOL_NAMES
    assert briefing.MUTATION_TOOL_NAMES - {"Edit", "Write", "MultiEdit", "NotebookEdit"} == {
        "write", "search_replace", "hashline_edit",
    }
