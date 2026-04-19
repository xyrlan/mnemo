"""Unit tests for core/briefing.py — per-session briefing generation (v0.3.1)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core import llm as llm_mod


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def _fake_llm_response(text: str) -> llm_mod.LLMResponse:
    return llm_mod.LLMResponse(
        text=text,
        total_cost_usd=0.0042,
        input_tokens=800,
        output_tokens=400,
        api_key_source="none",
        raw={"result": text},
    )


@pytest.fixture
def stub_llm(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {"calls": []}

    def fake_call(prompt, *, system, model, timeout):
        captured["calls"].append({
            "prompt": prompt,
            "system": system,
            "model": model,
            "timeout": timeout,
        })
        response = captured.get("response")
        if response is None:
            response = _fake_llm_response(
                "## TL;DR\nDid stuff.\n\n## What I did\n- edited foo.py\n"
            )
        return response

    monkeypatch.setattr(llm_mod, "call", fake_call)
    return captured


def _minimal_jsonl(tmp_path: Path, session_id: str = "abc123") -> Path:
    jsonl = tmp_path / f"{session_id}.jsonl"
    _write_jsonl(jsonl, [
        {
            "type": "user",
            "timestamp": "2026-04-14T10:00:00.000Z",
            "message": {"role": "user", "content": "add a retry helper"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-04-14T10:05:00.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I'll add the helper."},
                    {"type": "tool_use", "name": "Write",
                     "input": {"file_path": "retry.py", "content": "# ..."}},
                ],
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-04-14T10:42:00.000Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Done — wrote retry.py."}],
            },
        },
    ])
    return jsonl


def test_generate_briefing_writes_to_expected_path(tmp_vault: Path, tmp_path: Path, stub_llm):
    from mnemo.core import briefing

    jsonl = _minimal_jsonl(tmp_path / "jsonls", session_id="abc123")
    cfg = {"vaultRoot": str(tmp_vault), "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60}}

    out = briefing.generate_session_briefing(jsonl, agent="agent_a", cfg=cfg)

    expected = tmp_vault / "bots" / "agent_a" / "briefings" / "sessions" / "abc123.md"
    assert out == expected
    assert expected.exists()


def test_generate_briefing_creates_parent_dirs_on_demand(tmp_vault: Path, tmp_path: Path, stub_llm):
    from mnemo.core import briefing

    jsonl = _minimal_jsonl(tmp_path / "jsonls", session_id="fresh")
    cfg = {"vaultRoot": str(tmp_vault), "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60}}

    assert not (tmp_vault / "bots" / "agent_new").exists()
    briefing.generate_session_briefing(jsonl, agent="agent_new", cfg=cfg)

    assert (tmp_vault / "bots" / "agent_new" / "briefings" / "sessions").is_dir()


def test_generate_briefing_emits_frontmatter_with_spec_fields(tmp_vault: Path, tmp_path: Path, stub_llm):
    from mnemo.core import briefing

    jsonl = _minimal_jsonl(tmp_path / "jsonls", session_id="xyz999")
    cfg = {"vaultRoot": str(tmp_vault), "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60}}

    out = briefing.generate_session_briefing(jsonl, agent="agent_a", cfg=cfg)

    text = out.read_text()
    assert text.startswith("---\n")
    assert "type: briefing" in text
    assert "agent: agent_a" in text
    assert "session_id: xyz999" in text
    assert "date:" in text


def test_generate_briefing_computes_duration_from_event_timestamps(tmp_vault: Path, tmp_path: Path, stub_llm):
    from mnemo.core import briefing

    jsonl = _minimal_jsonl(tmp_path / "jsonls", session_id="sid")  # 10:00 → 10:42
    cfg = {"vaultRoot": str(tmp_vault), "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60}}

    out = briefing.generate_session_briefing(jsonl, agent="agent_a", cfg=cfg)

    text = out.read_text()
    assert "duration_minutes: 42" in text


def test_generate_briefing_includes_llm_body(tmp_vault: Path, tmp_path: Path, stub_llm):
    from mnemo.core import briefing

    stub_llm["response"] = _fake_llm_response(
        "## TL;DR\nImplemented retry helper.\n\n## Decisions made\n- Used exponential backoff.\n"
    )
    jsonl = _minimal_jsonl(tmp_path / "jsonls", session_id="sid")
    cfg = {"vaultRoot": str(tmp_vault), "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60}}

    out = briefing.generate_session_briefing(jsonl, agent="agent_a", cfg=cfg)

    text = out.read_text()
    assert "Implemented retry helper" in text
    assert "exponential backoff" in text


def test_generate_briefing_passes_jsonl_content_into_llm_prompt(tmp_vault: Path, tmp_path: Path, stub_llm):
    from mnemo.core import briefing

    jsonl = _minimal_jsonl(tmp_path / "jsonls", session_id="sid")
    cfg = {"vaultRoot": str(tmp_vault), "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60}}

    briefing.generate_session_briefing(jsonl, agent="agent_a", cfg=cfg)

    assert stub_llm["calls"], "LLM should be called once"
    call = stub_llm["calls"][0]
    assert "add a retry helper" in call["prompt"]
    assert "Done — wrote retry.py." in call["prompt"]
    assert call["system"]  # non-empty system prompt
    assert call["model"] == "claude-haiku-4-5"


def test_generate_briefing_skips_malformed_jsonl_lines(tmp_vault: Path, tmp_path: Path, stub_llm):
    """A broken line in the middle must not crash the generator."""
    from mnemo.core import briefing

    jsonl = tmp_path / "jsonls" / "sid.jsonl"
    jsonl.parent.mkdir(parents=True)
    jsonl.write_text(
        json.dumps({
            "type": "user",
            "timestamp": "2026-04-14T10:00:00.000Z",
            "message": {"role": "user", "content": "start"},
        })
        + "\nthis is not json\n"
        + json.dumps({
            "type": "assistant",
            "timestamp": "2026-04-14T10:05:00.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Edit",
                     "input": {"file_path": "x.py"}},
                ],
            },
        })
        + "\n"
    )
    cfg = {"vaultRoot": str(tmp_vault), "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60}}

    out = briefing.generate_session_briefing(jsonl, agent="agent_a", cfg=cfg)

    assert out.exists()


def test_briefing_system_prompt_mentions_handoff_semantics():
    from mnemo.core.extract import prompts

    assert hasattr(prompts, "BRIEFING_SYSTEM_PROMPT")
    sysp = prompts.BRIEFING_SYSTEM_PROMPT.lower()
    # The system prompt must frame the task as a handoff from one shift to the next.
    assert "handoff" in sysp or "hand off" in sysp or "brief" in sysp
    # It must describe the opinionated section structure.
    assert "tl;dr" in sysp
    assert "decisions" in sysp
    assert "resume at" in sysp


def test_briefing_system_prompt_lists_headers_bare_without_inline_descriptions():
    """Regression: 2026-04-14 dogfood showed the LLM was echoing instruction
    text into actual section headers (e.g., emitting
    `## TL;DR — 3-5 sentences summarizing the session.` as its section header
    instead of a bare `## TL;DR`). The prompt must list the required headers
    as bare markdown lines, separate from the description of what goes under
    each one, so the model has a clean literal to copy.
    """
    from mnemo.core.extract import prompts

    sysp = prompts.BRIEFING_SYSTEM_PROMPT
    required_bare_headers = [
        "## TL;DR",
        "## What I did",
        "## Decisions made",
        "## Dead ends",
        "## Open questions",
        "## State at end of session",
        "## Context I'd forget otherwise",
    ]
    for header in required_bare_headers:
        assert header in sysp, f"prompt must mention the '{header}' header"
        bad_pattern = f"{header} —"
        assert bad_pattern not in sysp, (
            f"section header '{header}' must be listed bare in the prompt, "
            f"not followed by ' — description' inline — the LLM echoes "
            f"whatever comes after the header name into its own output"
        )


def test_build_briefing_prompt_includes_rendered_events():
    from mnemo.core.extract import prompts
    from mnemo.core.transcript import flatten_transcript_events

    events = [
        {"type": "user", "message": {"role": "user", "content": "hello"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "world"}],
            },
        },
    ]
    prompt = prompts.build_briefing_prompt(flatten_transcript_events(events))
    assert "hello" in prompt
    assert "world" in prompt


def test_build_briefing_prompt_renders_tool_use_and_tool_result_blocks():
    from mnemo.core.extract import prompts
    from mnemo.core.transcript import flatten_transcript_events

    events = [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {"path": "/tmp"}},
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "content": "file contents here"},
                    {"type": "tool_result", "content": "x" * 600},  # truncation branch
                ],
            },
        },
    ]
    prompt = prompts.build_briefing_prompt(flatten_transcript_events(events))
    assert "[tool_use: Read]" in prompt
    assert "file contents here" in prompt
    assert "…" in prompt  # long tool_result truncated


def test_build_briefing_prompt_skips_non_dict_events_and_empty_content():
    from mnemo.core.extract import prompts
    from mnemo.core.transcript import flatten_transcript_events

    events = [
        "not a dict",
        {"type": "assistant", "message": {"role": "assistant", "content": []}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "unknown"}]}},
        {"type": "user", "message": {"role": "user", "content": "kept"}},
    ]
    prompt = prompts.build_briefing_prompt(flatten_transcript_events(events))
    assert "kept" in prompt
    assert "not a dict" not in prompt


def test_generate_briefing_falls_back_when_llm_returns_empty_body(tmp_vault: Path, tmp_path: Path, stub_llm):
    from mnemo.core import briefing

    stub_llm["response"] = _fake_llm_response("")  # empty LLM output
    jsonl = _minimal_jsonl(tmp_path / "jsonls", session_id="sid")
    cfg = {"vaultRoot": str(tmp_vault), "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60}}

    out = briefing.generate_session_briefing(jsonl, agent="agent_a", cfg=cfg)

    text = out.read_text()
    assert "empty briefing" in text


def test_generate_briefing_handles_malformed_timestamps_gracefully(tmp_vault: Path, tmp_path: Path, stub_llm):
    from mnemo.core import briefing

    jsonl = tmp_path / "jsonls" / "sid.jsonl"
    jsonl.parent.mkdir(parents=True)
    jsonl.write_text(
        json.dumps({
            "type": "user",
            "timestamp": "not-a-real-timestamp",
            "message": {"role": "user", "content": "broken ts"},
        })
        + "\n"
        + json.dumps({
            "type": "assistant",
            "timestamp": None,
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Edit",
                     "input": {"file_path": "x.py"}},
                ],
            },
        })
        + "\n"
    )
    cfg = {"vaultRoot": str(tmp_vault), "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60}}

    out = briefing.generate_session_briefing(jsonl, agent="agent_a", cfg=cfg)

    text = out.read_text()
    # Duration defaults to 0 when no valid timestamps are present.
    assert "duration_minutes: 0" in text


def test_generate_briefing_handles_unreadable_jsonl(tmp_vault: Path, tmp_path: Path, stub_llm, monkeypatch):
    from mnemo.core import briefing

    missing = tmp_path / "does-not-exist.jsonl"
    cfg = {"vaultRoot": str(tmp_vault), "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60}}

    # Missing file → _load_jsonl_events returns [] → zero mutations → skip gate.
    # Prior behavior (writing an empty-shell briefing) was the exact noise this
    # gate exists to prevent.
    out = briefing.generate_session_briefing(missing, agent="agent_a", cfg=cfg)
    assert out is None
    assert not stub_llm["calls"]


def _jsonl_with_events(path: Path, events: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return path


def test_generate_briefing_skips_when_session_has_no_file_mutations(
    tmp_vault: Path, tmp_path: Path, stub_llm
):
    """A session with only reads, bash, and text chat is not worth a briefing.

    The fix addresses real dogfood noise: sessions where the user only ran
    /context or called MCP read tools produced empty-shell briefings that
    polluted the extraction cluster. Gate on at least one file-mutation
    tool_use before spending an LLM call.
    """
    from mnemo.core import briefing

    jsonl = _jsonl_with_events(tmp_path / "jsonls" / "noop.jsonl", [
        {"type": "user", "timestamp": "2026-04-15T09:00:00.000Z",
         "message": {"role": "user", "content": "check context"}},
        {"type": "assistant", "timestamp": "2026-04-15T09:00:05.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "name": "Read", "input": {"path": "/tmp/x"}},
         ]}},
        {"type": "assistant", "timestamp": "2026-04-15T09:00:07.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
         ]}},
    ])
    cfg = {"vaultRoot": str(tmp_vault), "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60}}

    out = briefing.generate_session_briefing(jsonl, agent="agent_a", cfg=cfg)

    assert out is None, "briefing with no mutations must return None"
    assert not stub_llm["calls"], "LLM must not be called for noop sessions"
    briefings_dir = tmp_vault / "bots" / "agent_a" / "briefings" / "sessions"
    assert not briefings_dir.exists() or not any(briefings_dir.iterdir()), \
        "no briefing file should be written for noop sessions"


@pytest.mark.parametrize("mutation_tool", ["Edit", "Write", "MultiEdit", "NotebookEdit"])
def test_generate_briefing_runs_when_session_has_any_file_mutation(
    tmp_vault: Path, tmp_path: Path, stub_llm, mutation_tool: str
):
    from mnemo.core import briefing

    jsonl = _jsonl_with_events(tmp_path / "jsonls" / f"{mutation_tool}.jsonl", [
        {"type": "user", "timestamp": "2026-04-15T09:00:00.000Z",
         "message": {"role": "user", "content": "do the thing"}},
        {"type": "assistant", "timestamp": "2026-04-15T09:05:00.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "name": mutation_tool, "input": {"file_path": "/tmp/x"}},
         ]}},
    ])
    cfg = {"vaultRoot": str(tmp_vault), "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60}}

    out = briefing.generate_session_briefing(jsonl, agent="agent_a", cfg=cfg)

    assert out is not None, f"briefing with {mutation_tool} must not be skipped"
    assert out.exists()
    assert len(stub_llm["calls"]) == 1


def test_cmd_briefing_logs_and_returns_1_on_llm_failure(tmp_vault: Path, tmp_path: Path, monkeypatch):
    """cmd_briefing must swallow exceptions and log them (fire-and-forget semantics)."""
    from mnemo import cli
    from mnemo.core import briefing as briefing_mod

    cfg = {"vaultRoot": str(tmp_vault), "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60}}
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: cfg)
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda _cfg: tmp_vault)

    def boom(*a, **kw):
        raise RuntimeError("synthetic LLM failure")

    monkeypatch.setattr(briefing_mod, "generate_session_briefing", boom)

    jsonl = tmp_path / "sid.jsonl"
    jsonl.write_text("{}\n")

    rc = cli.main(["briefing", str(jsonl), "agent_a"])
    assert rc == 1
    assert (tmp_vault / ".errors.log").exists()
