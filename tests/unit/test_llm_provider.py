"""#358: every model call goes through the provider ``extraction.provider`` names."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mnemo.core import config, llm


def _envelope(text: str) -> str:
    return json.dumps([
        {"type": "system", "subtype": "init", "apiKeySource": "none"},
        {"type": "result", "result": text, "total_cost_usd": 0.01,
         "usage": {"input_tokens": 3, "cache_read_input_tokens": 4, "output_tokens": 5}},
    ])


# ------------------------------------------------------------------ resolve


def test_default_config_names_the_claude_cli_provider():
    assert config.DEFAULTS["extraction"]["provider"] == "claude-cli"
    assert llm.DEFAULT_PROVIDER == "claude-cli"


@pytest.mark.parametrize("cfg", [
    None,
    {},
    {"extraction": {}},
    {"extraction": {"provider": None}},
    {"extraction": {"provider": ""}},
    {"extraction": {"provider": "claude-cli"}},
])
def test_absent_or_default_provider_resolves_to_claude_cli(cfg):
    assert llm.resolve(cfg) is llm.PROVIDERS["claude-cli"]


@pytest.mark.parametrize("name", ["codex", "Claude-CLI", 3, ["claude-cli"]])
def test_unknown_provider_fails_at_resolve_time(name):
    with pytest.raises(llm.UnknownProviderError) as info:
        llm.resolve({"extraction": {"provider": name}})
    assert "extraction.provider" in str(info.value)
    assert "claude-cli" in str(info.value)  # names what would have worked


def test_unknown_provider_is_a_config_and_environmental_error():
    # ValueError: it is a bad config value. LLMSubprocessError: backfill's
    # `_environmental` split aborts the sweep instead of charging every
    # transcript with the same error.
    from mnemo.cli.commands.backfill import _environmental

    exc = llm.UnknownProviderError("x")
    assert isinstance(exc, ValueError)
    assert _environmental(exc)


def test_default_provider_is_the_unchanged_claude_print_call(monkeypatch):
    """Same argv, env, input and parsed response as ``llm.call`` itself."""
    runs: list[tuple] = []

    def fake_run(argv, **kwargs):
        runs.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=_envelope("hi"), stderr="")

    monkeypatch.setattr(llm, "_subprocess_run", fake_run)
    direct = llm.call("p", system="s", model="claude-haiku-4-5", timeout=9)
    via = llm.resolve({})("p", system="s", model="claude-haiku-4-5", timeout=9)

    assert runs[0] == runs[1]
    assert runs[0][0][1] == "--print"
    assert via == direct
    assert via.provider == "claude-cli"
    assert (via.total_cost_usd, via.input_tokens, via.output_tokens) == (0.01, 7, 5)


def test_resolved_default_follows_a_patched_llm_call(monkeypatch):
    """Resolution happens at call time, so ``monkeypatch.setattr(llm, "call")``
    — what the existing suite does everywhere — still intercepts."""
    marker = object()
    monkeypatch.setattr(llm, "call", lambda prompt, **kw: marker)
    assert llm.resolve(None)("p", system=None, model="m", timeout=1) is marker


def test_access_log_names_the_provider(tmp_path: Path):
    from mnemo.core.mcp import access_log

    resp = llm.LLMResponse(text="", total_cost_usd=None, input_tokens=None,
                           output_tokens=None, api_key_source=None, raw={},
                           provider="stub")
    access_log.record_llm_call(tmp_path, resp, purpose="t", model="m",
                               project=None, agent="a", elapsed_ms=1.0)
    rows = [json.loads(l) for l in
            (tmp_path / ".mnemo" / "mcp-access-log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["provider"] == "stub"


# ------------------------------------------------- a stub provider, no claude


_QUOTE = "always use yarn, never npm install"


def _stub_provider(calls: list):
    def provider(prompt, *, system, model, timeout):
        calls.append({"system": system, "model": model, "timeout": timeout})
        text = json.dumps({"pages": [{
            "slug": "use-yarn", "name": "Use yarn", "description": "d",
            "type": "feedback", "body": "yarn b",
            "source_files": ["bots/agent-a/briefings/sessions/s1.md"],
            "evidence": {"quote": _QUOTE, "source": "bots/agent-a/briefings/sessions/s1.md"},
        }]})
        return llm.LLMResponse(text=text, total_cost_usd=None, input_tokens=None,
                               output_tokens=None, api_key_source=None,
                               raw={}, provider="stub")
    return provider


@pytest.fixture
def no_claude(monkeypatch, tmp_path):
    """No ``claude`` anywhere: empty PATH, and the CLI path refuses to run."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert shutil.which("claude") is None

    def refuse(*a, **kw):
        raise AssertionError("the claude-cli path must not be reached")

    monkeypatch.setattr(llm, "call", refuse)
    monkeypatch.setattr(llm, "_subprocess_run", refuse)


def _extract_cfg(vault: Path, provider: str) -> dict:
    return {
        "vaultRoot": str(vault),
        "extraction": {"provider": provider, "model": "stub-model", "chunkSize": 10,
                       "subprocessTimeout": 5, "costSoftCap": None},
    }


def test_stub_provider_satisfies_extraction_end_to_end(populated_vault, no_claude, monkeypatch):
    from mnemo.core.extract import run_extraction

    briefing = populated_vault / "bots" / "agent-a" / "briefings" / "sessions" / "s1.md"
    briefing.parent.mkdir(parents=True, exist_ok=True)
    briefing.write_text(
        "---\ntype: briefing\nagent: agent-a\nsession_id: s1\n---\n\n"
        "# Briefing — agent-a — s1\n\n## Corrections\n"
        f'- "{_QUOTE}" → Use yarn\n',
        encoding="utf-8",
    )
    calls: list = []
    monkeypatch.setitem(llm.PROVIDERS, "stub", _stub_provider(calls))

    summary = run_extraction(_extract_cfg(populated_vault, "stub"))

    assert summary.failed_chunks == 0
    assert summary.llm_calls == len(calls) >= 1
    assert calls[0]["model"] == "stub-model" and calls[0]["timeout"] == 5
    assert (populated_vault / "shared" / "feedback" / "use-yarn.md").exists()
    # Unknown usage stays unknown in the summary rather than breaking it.
    assert summary.total_cost_usd == 0.0
    rows = [json.loads(l) for l in (populated_vault / ".mnemo" / "mcp-access-log.jsonl")
            .read_text(encoding="utf-8").splitlines()]
    assert [r["provider"] for r in rows if r.get("tool") == "llm.call"] == ["stub"] * len(calls)


def test_unknown_provider_stops_extraction_before_it_writes(populated_vault, no_claude):
    from mnemo.core.extract import run_extraction

    with pytest.raises(llm.UnknownProviderError):
        run_extraction(_extract_cfg(populated_vault, "nope"))
    assert not (populated_vault / "shared" / "project").exists()
    assert not (populated_vault / ".mnemo" / "extraction-state.json").exists()


def _transcript(tmp_path: Path) -> Path:
    events = [
        {"type": "user", "timestamp": "2026-09-17T10:00:00Z",
         "message": {"role": "user", "content": [{"type": "text", "text": "no, use pathlib"}]}},
        {"type": "assistant", "timestamp": "2026-09-17T10:01:00Z",
         "message": {"role": "assistant",
                     "content": [{"type": "tool_use", "name": "Edit", "id": "t0", "input": {}}]}},
    ]
    p = tmp_path / "sess-1.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return p


def test_briefing_goes_through_the_provider(tmp_vault, tmp_path, no_claude, monkeypatch):
    from mnemo.core import briefing

    calls: list = []

    def provider(prompt, *, system, model, timeout):
        calls.append(model)
        return llm.LLMResponse(text="## TL;DR\n\nstub", total_cost_usd=None,
                               input_tokens=None, output_tokens=None,
                               api_key_source=None, raw={}, provider="stub")

    monkeypatch.setitem(llm.PROVIDERS, "stub", provider)
    cfg = {"vaultRoot": str(tmp_vault),
           "extraction": {"provider": "stub", "model": "stub-model"}}
    out = briefing.generate_session_briefing(_transcript(tmp_path), "agent-a", cfg)
    assert calls == ["stub-model"]
    assert "stub" in out.read_text(encoding="utf-8")


def test_briefing_with_unknown_provider_raises(tmp_vault, tmp_path, no_claude):
    from mnemo.core import briefing

    cfg = {"vaultRoot": str(tmp_vault), "extraction": {"provider": "nope"}}
    with pytest.raises(llm.UnknownProviderError):
        briefing.generate_session_briefing(_transcript(tmp_path), "agent-a", cfg)


def test_harvest_goes_through_the_provider(tmp_path, no_claude, monkeypatch):
    from mnemo.core.backfill import harvest

    calls: list = []
    monkeypatch.setitem(llm.PROVIDERS, "stub", _stub_provider(calls))
    root = tmp_path / "vault"
    (root / "bots").mkdir(parents=True)
    cfg = {"vaultRoot": str(root),
           "extraction": {"provider": "stub", "model": "stub-model", "subprocessTimeout": 5},
           "backfill": {"minFileMutations": 1}}
    harvest.harvest_session(_transcript(tmp_path), "agent-a", cfg)
    assert [c["model"] for c in calls] == ["stub-model"]


def test_friction_link_default_runner_goes_through_the_provider(no_claude, monkeypatch):
    from mnemo.core.friction import link

    seen: list = []

    def provider(prompt, *, system, model, timeout):
        seen.append((model, timeout))
        return llm.LLMResponse(text='{"links": []}', total_cost_usd=None,
                               input_tokens=None, output_tokens=None,
                               api_key_source=None, raw={}, provider="stub")

    monkeypatch.setitem(llm.PROVIDERS, "stub", provider)
    monkeypatch.setattr(
        "mnemo.core.config.load_config",
        lambda: {"extraction": {"provider": "stub", "model": "stub-model", "subprocessTimeout": 5}},
    )
    assert link._default_runner("p", system="s") == '{"links": []}'
    assert seen == [("stub-model", 5)]


def test_friction_link_unknown_provider_is_not_swapped_for_the_default(no_claude, monkeypatch):
    from mnemo.core.friction import link

    monkeypatch.setattr("mnemo.core.config.load_config",
                        lambda: {"extraction": {"provider": "nope"}})
    with pytest.raises(llm.UnknownProviderError):
        link._default_runner("p", system="s")
