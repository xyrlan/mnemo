"""Integration: SessionEnd → detached briefing spawn → CLI writes the file.

This exercises the full chain end-to-end with the LLM stubbed out:
1. SessionEnd hook runs with `briefings.enabled=true` in cfg.
2. It resolves the session jsonl via the Claude Code path convention.
3. It spawns `mnemo briefing <jsonl> <agent>` detached.
4. We run that subprocess argv in-process (via cli.main) and verify the
   briefing file lands under bots/<agent>/briefings/sessions/<sid>.md.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest


def _session_payload(session_id: str, cwd: str) -> str:
    return json.dumps({"session_id": session_id, "cwd": cwd, "reason": "exit"})


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_session_end_spawns_briefing_and_cli_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from mnemo import cli
    from mnemo.core import llm as llm_mod
    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    (vault / "bots" / "fake-project" / "memory").mkdir(parents=True)

    home = tmp_path / "home"
    fake_cwd = "/tmp/fake-project"
    encoded = fake_cwd.replace("/", "-")
    jsonl_dir = home / ".claude" / "projects" / encoded
    _write_jsonl(jsonl_dir / "sidXYZ.jsonl", [
        {
            "type": "user",
            "timestamp": "2026-04-14T10:00:00.000Z",
            "message": {"role": "user", "content": "build retry helper"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-04-14T10:30:00.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Writing retry.py."},
                    {"type": "tool_use", "name": "Write",
                     "input": {"file_path": "retry.py", "content": "# ..."}},
                ],
            },
        },
    ])
    monkeypatch.setenv("HOME", str(home))

    cfg = {
        "vaultRoot": str(vault),
        "capture": {"sessionStartEnd": False},
        "extraction": {
            "auto": {"enabled": False},
            "model": "claude-haiku-4-5",
            "subprocessTimeout": 60,
        },
        "briefings": {"enabled": True},
    }
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: cfg)
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda _cfg: vault)
    monkeypatch.setattr("mnemo.core.mirror.mirror_all", lambda _cfg: None)

    captured: dict = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs

    monkeypatch.setattr("subprocess.Popen", FakePopen)

    def fake_llm_call(prompt, *, system, model, timeout):
        return llm_mod.LLMResponse(
            text="## TL;DR\nBuilt retry helper.\n\n## Decisions made\n- Used exponential backoff.\n",
            total_cost_usd=0.003,
            input_tokens=500,
            output_tokens=200,
            api_key_source="none",
            raw={},
        )

    monkeypatch.setattr(llm_mod, "call", fake_llm_call)

    # 1. Fire the SessionEnd hook.
    monkeypatch.setattr("sys.stdin", io.StringIO(_session_payload("sidXYZ", fake_cwd)))
    exit_code = session_end.main()
    assert exit_code == 0

    # 2. The hook should have spawned `python -m mnemo briefing <jsonl> fake-project`.
    argv = captured.get("argv")
    assert argv is not None, "SessionEnd must spawn the briefing subprocess"
    assert "briefing" in argv
    assert "fake-project" in argv
    jsonl_arg_idx = argv.index("briefing") + 1
    jsonl_arg = argv[jsonl_arg_idx]
    assert "sidXYZ.jsonl" in jsonl_arg

    # 3. Simulate the detached subprocess by running the CLI in-process.
    rc = cli.main(["briefing", jsonl_arg, "fake-project"])
    assert rc == 0

    # 4. The briefing file must exist and carry the expected frontmatter/body.
    briefing_path = vault / "bots" / "fake-project" / "briefings" / "sessions" / "sidXYZ.md"
    assert briefing_path.exists(), f"briefing not written to {briefing_path}"
    text = briefing_path.read_text()
    assert "type: briefing" in text
    assert "agent: fake-project" in text
    assert "session_id: sidXYZ" in text
    assert "Built retry helper" in text
    assert "exponential backoff" in text


def test_session_end_does_not_spawn_briefing_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    (vault / "bots" / "fake-project" / "memory").mkdir(parents=True)

    # Even with a valid jsonl on disk, disabled flag must short-circuit.
    home = tmp_path / "home"
    fake_cwd = "/tmp/fake"
    encoded = fake_cwd.replace("/", "-")
    jsonl_dir = home / ".claude" / "projects" / encoded
    _write_jsonl(jsonl_dir / "sid.jsonl", [
        {"type": "user", "timestamp": "2026-04-14T10:00:00.000Z",
         "message": {"role": "user", "content": "hi"}},
    ])
    monkeypatch.setenv("HOME", str(home))

    cfg = {
        "vaultRoot": str(vault),
        "capture": {"sessionStartEnd": False},
        "extraction": {"auto": {"enabled": False}},
        "briefings": {"enabled": False},
    }
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: cfg)
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda _cfg: vault)
    monkeypatch.setattr("mnemo.core.mirror.mirror_all", lambda _cfg: None)

    calls: list = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr("sys.stdin", io.StringIO(_session_payload("sid", fake_cwd)))

    session_end.main()
    assert calls == [], "briefing must not spawn when briefings.enabled=False"
