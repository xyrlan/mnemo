"""Briefing pipeline: user turns go in, only verified corrections come out."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core import briefing as briefing_mod
from mnemo.core import corrections as corrections_mod
from mnemo.core import llm as llm_mod
from mnemo.core.extract import prompts
from mnemo.core.extract.prompts.render import _USER_TURN_MAX_CHARS
from mnemo.core.extract.prompts.templates.briefing import BRIEFING_SYSTEM_PROMPT


def _events():
    return [
        {"type": "user", "timestamp": "2026-09-01T10:00:00.000Z",
         "message": {"role": "user", "content": "add a retry helper"}},
        {"type": "assistant", "timestamp": "2026-09-01T10:01:00.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "name": "Write", "input": {"file_path": "retry.py"}}]}},
        {"type": "user", "timestamp": "2026-09-01T10:02:00.000Z",
         "message": {"role": "user", "content": "no — never retry on 4xx, only on 5xx"}},
        {"type": "user", "timestamp": "2026-09-01T10:03:00.000Z",
         "message": {"role": "user", "content": "<task-notification>done</task-notification>"}},
    ]


LLM_BODY = (
    "## TL;DR\nRetry helper.\n\n"
    "## Decisions made\n- axios interceptor. **Why:** exists.\n\n"
    "## Corrections\n"
    '- "never retry on 4xx, only on 5xx" → Retry only on 5xx\n'
    '- "always use tabs" → Use tabs\n\n'
    "## Dead ends\n- none\n"
)


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: root)
    return root


def _write_jsonl(path: Path) -> Path:
    path.write_text("\n".join(json.dumps(e) for e in _events()) + "\n", encoding="utf-8")
    return path


def test_system_prompt_defines_corrections_section():
    assert "## Corrections" in BRIEFING_SYSTEM_PROMPT
    assert "verbatim" in BRIEFING_SYSTEM_PROMPT.lower()


def test_prompt_lists_numbered_user_turns_without_machine_turns():
    text = prompts.build_briefing_prompt("[user] hi", user_turns=["add a retry helper", "no — never retry"])
    assert "=== USER TURNS" in text
    assert "[1] add a retry helper" in text
    assert "[2] no — never retry" in text


def _shown_turn_line(text: str, marker: str = "[1] ") -> str:
    """The quotable text of turn [1] — the numbered line, prefix stripped."""
    for line in text.splitlines():
        if line.startswith(marker):
            return line[len(marker):]
    raise AssertionError(f"no {marker!r} line in prompt")


def test_prompt_truncates_long_turns_to_600_chars():
    text = prompts.build_briefing_prompt("x", user_turns=["a" * 700])
    shown = _shown_turn_line(text)
    assert len(shown) <= 600
    assert "a" * 601 not in text
    assert "(turn continues — 700 chars total; quote only the text shown above)" in text


def test_prompt_shows_a_600_char_turn_whole_without_a_note():
    turn = "a" * 600
    text = prompts.build_briefing_prompt("x", user_turns=[turn])
    assert _shown_turn_line(text) == turn
    assert "turn continues" not in text


def test_truncated_turn_tail_still_verifies_against_the_full_turn():
    full = ("word " * 200).strip()  # 999 chars, well over the cut
    assert len(full) > _USER_TURN_MAX_CHARS
    text = prompts.build_briefing_prompt("x", user_turns=[full])
    shown = _shown_turn_line(text)
    quote = shown[-30:]
    assert corrections_mod.quote_matches_turn(quote, full)


def test_briefing_keeps_verified_and_drops_fabricated(vault: Path, tmp_path: Path, monkeypatch):
    captured = {}

    def fake_call(prompt, *, system, model, timeout):
        captured["prompt"] = prompt
        return llm_mod.LLMResponse(text=LLM_BODY, total_cost_usd=0.0, input_tokens=1,
                                   output_tokens=1, api_key_source="none", raw={})
    monkeypatch.setattr(llm_mod, "call", fake_call)
    logged = []
    monkeypatch.setattr("mnemo.core.errors.log_error", lambda root, where, exc: logged.append((where, str(exc))))

    jsonl = _write_jsonl(tmp_path / "sess1.jsonl")
    out = briefing_mod.generate_session_briefing(jsonl, "proj", {"extraction": {}})
    text = out.read_text(encoding="utf-8")

    assert "[1] add a retry helper" in captured["prompt"]
    assert "task-notification" not in captured["prompt"].split("=== USER TURNS")[1].split("=== END USER TURNS")[0]
    assert '"never retry on 4xx, only on 5xx" → Retry only on 5xx' in text
    assert "always use tabs" not in text
    assert "corrections: 1\n" in text.split("---")[1]
    assert "briefing.corrections_rejected" in [w for w, _ in logged]


def test_briefing_without_corrections_has_no_section_and_zero_count(vault, tmp_path, monkeypatch):
    monkeypatch.setattr(llm_mod, "call", lambda *a, **k: llm_mod.LLMResponse(
        text="## TL;DR\nx\n", total_cost_usd=0.0, input_tokens=1, output_tokens=1,
        api_key_source="none", raw={}))
    jsonl = _write_jsonl(tmp_path / "sess2.jsonl")
    text = briefing_mod.generate_session_briefing(jsonl, "proj", {"extraction": {}}).read_text(encoding="utf-8")
    assert "## Corrections" not in text
    assert "corrections: 0\n" in text.split("---")[1]


def test_briefing_survives_a_corrections_failure(vault, tmp_path, monkeypatch):
    monkeypatch.setattr(llm_mod, "call", lambda *a, **k: llm_mod.LLMResponse(
        text=LLM_BODY, total_cost_usd=0.0, input_tokens=1, output_tokens=1,
        api_key_source="none", raw={}))

    def boom(*a, **k):
        raise RuntimeError("verify exploded")
    monkeypatch.setattr("mnemo.core.corrections.verify", boom)
    logged = []
    monkeypatch.setattr("mnemo.core.errors.log_error", lambda root, where, exc: logged.append((where, str(exc))))

    jsonl = _write_jsonl(tmp_path / "sess3.jsonl")
    out = briefing_mod.generate_session_briefing(jsonl, "proj", {"extraction": {}})
    text = out.read_text(encoding="utf-8")

    assert "## Corrections" not in text
    assert "corrections: 0\n" in text.split("---")[1]
    assert "## TL;DR" in text  # the briefing itself survived
    assert "briefing.corrections" in [w for w, _ in logged]
