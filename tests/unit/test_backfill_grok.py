"""``mnemo backfill`` over Grok sessions: each one is filed under its own id.

Every Grok history is ``chat_history.jsonl``, so a ledger keyed by the file's
stem filed every Grok session under one ``chat_history`` entry: a sweep
re-harvested each of them, with a model call, every time it ran, and their
corrections were recorded under ``chat_history``. Keyed by
``briefing.session_id_for`` instead, a Claude session's key stays its stem byte
for byte, so a ledger written before this still matches.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List

import pytest

from mnemo.cli.commands import backfill as cmd
from mnemo.core import llm
from mnemo.core.backfill import discover, harvest, ledger
from mnemo.core.friction import capture
from mnemo.core.friction import ledger as friction_ledger
from mnemo.core.grok import paths as grok_paths
from mnemo.core.mcp import access_log
from mnemo.core.sessions import parents
from tests.unit._grok_history import write_session

REPO = "/Users/you/github/app"
GROK_A = "01a144ca-899c-78a1-a925-c8f76ff7de8f"
GROK_B = "01a144ca-9a00-7000-8000-00000000beef"  # same first eight: UUIDv7 neighbours
CLAUDE = "7d3c1f0e-5b2a-4c8e-9f10-2a3b4c5d6e7f"


def _response(text: str) -> llm.LLMResponse:
    return llm.LLMResponse(text=text, total_cost_usd=0.0, input_tokens=1,
                           output_tokens=1, api_key_source="none", raw={})


@pytest.fixture
def world(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    for name in ("GROK_HOME", "GROK_SESSION_ID", "MNEMO_AGENT"):
        monkeypatch.delenv(name, raising=False)
    vault = tmp_home / "vault"
    (vault / "bots").mkdir(parents=True)
    (vault / ".mnemo").mkdir()
    cfg = {
        "vaultRoot": str(vault),
        "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60},
        "backfill": {"enabled": True, "installCap": 20, "minFileMutations": 1},
    }
    monkeypatch.setattr(cmd.cfg_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cmd, "_current_project", lambda: "app")
    monkeypatch.setattr(discover, "agent_for_cwd", lambda cwd: "app" if cwd == REPO else "other")
    monkeypatch.setattr(access_log, "_load_telemetry_config", lambda: (False, 0))

    a = write_session(tmp_home / ".grok", cwd=REPO, session_id=GROK_A)
    b = write_session(tmp_home / ".grok-work", cwd=REPO, session_id=GROK_B)
    claude = tmp_home / ".claude" / "projects" / discover.encode_cwd(REPO) / f"{CLAUDE}.jsonl"
    claude.parent.mkdir(parents=True)
    claude.write_text("".join(json.dumps(e) + "\n" for e in [
        {"type": "user", "cwd": REPO, "message": {"role": "user", "content": "fix it"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Edit", "id": "t0", "input": {}}]}},
    ]), encoding="utf-8")

    prompts: List[str] = []

    def fake_call(prompt, **kwargs):
        prompts.append(prompt)
        return _response(json.dumps({"pages": []}))

    monkeypatch.setattr(harvest.llm, "call", fake_call)
    return cfg, vault, {"a": a, "b": b, "claude": claude}, prompts


def _args(**kw) -> argparse.Namespace:
    base = dict(all=False, dry_run=False, project=None, limit=None,
                install_run=False, yes=True, retry_failed=False)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.mark.parametrize("install_run", [False, True])
def test_a_sweep_harvests_each_grok_session_once_and_a_second_spends_nothing(world, install_run):
    _cfg, vault, made, prompts = world

    assert cmd.cmd_backfill(_args(install_run=install_run)) == 0
    assert len(prompts) == 3  # both Grok sessions and the Claude one

    sessions = ledger.load(vault)["sessions"]
    assert sorted(sessions) == sorted([GROK_A, GROK_B, CLAUDE])
    assert "chat_history" not in sessions
    assert sessions[GROK_A]["hash"] == ledger.transcript_hash(made["a"])

    prompts.clear()
    assert cmd.cmd_backfill(_args(install_run=install_run)) == 0
    assert prompts == []


def test_a_grok_session_that_grew_is_harvested_again_alone(world):
    _cfg, vault, made, prompts = world
    cmd.cmd_backfill(_args())
    prompts.clear()

    with made["b"].open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "assistant", "content": "more", "model_id": "grok-4.7"}) + "\n")
    cmd.cmd_backfill(_args())

    assert len(prompts) == 1


def test_a_claude_key_is_its_stem_and_an_old_ledger_line_still_matches(tmp_path: Path):
    transcript = tmp_path / f"{CLAUDE}.jsonl"
    transcript.write_text('{"type":"user"}\n', encoding="utf-8")
    # A ledger written before this change: keyed by `Path(path).stem`.
    led = {"sessions": {CLAUDE: {"status": "done", "attempts": 0, "produced": 0,
                                 "hash": ledger.transcript_hash(transcript)}}}

    assert ledger.session_key(transcript) == transcript.stem == CLAUDE
    assert ledger.should_harvest(led, transcript) is False

    ledger.mark_done(led, transcript, produced=2)
    assert list(led["sessions"]) == [CLAUDE]


def test_failures_are_counted_per_grok_session(tmp_home: Path):
    a = write_session(tmp_home / ".grok", cwd=REPO, session_id=GROK_A)
    b = write_session(tmp_home / ".grok", cwd=REPO, session_id=GROK_B)
    led: dict = {}
    for _ in range(ledger.MAX_ATTEMPTS):
        ledger.mark_failed(led, a, "boom")

    assert ledger.attempts_exhausted(led, a) is True
    assert ledger.should_harvest(led, b) is True
    assert led["sessions"][GROK_A]["attempts"] == ledger.MAX_ATTEMPTS


def test_updates_jsonl_names_the_same_entry_as_its_history(tmp_home: Path):
    history = write_session(tmp_home / ".grok", cwd=REPO, session_id=GROK_A)
    updates = history.with_name(grok_paths.UPDATES)

    assert ledger.session_key(updates) == GROK_A
    assert ledger.transcript_hash(updates) == ledger.transcript_hash(history)


def test_a_harvested_grok_page_names_its_session(world, monkeypatch):
    cfg, vault, made, _prompts = world
    page = {"slug": "use-yarn", "type": "feedback", "name": "Use yarn",
            "description": "yarn, not npm", "body": "Use yarn."}
    monkeypatch.setattr(harvest.llm, "call", lambda prompt, **kw: _response(json.dumps({"pages": [page]})))

    (written,) = harvest.harvest_session(made["a"], "app", cfg)

    assert f"originSessionId: {GROK_A}\n" in written.read_text(encoding="utf-8")


# --- friction capture -------------------------------------------------------


def _no_edit_grok_records() -> list:
    def user(i, text):
        return {"type": "user", "content": [{"type": "text", "text": f"<user_query>\n{text}\n</user_query>"}],
                "prompt_index": i}

    return [
        {"type": "system", "content": "You are Grok."},
        user(0, "how should I install the deps here?"),
        {"type": "assistant", "content": "Run npm install.", "model_id": "grok-4.7"},
        user(1, "never use npm in this repo, always yarn"),
        {"type": "assistant", "content": "Understood.", "model_id": "grok-4.7"},
    ]


@pytest.fixture
def friction_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: root)
    monkeypatch.setattr(access_log, "_load_telemetry_config", lambda: (True, 1_048_576))
    return root


def test_a_grok_sessions_corrections_are_recorded_under_its_id(friction_vault, tmp_home, monkeypatch):
    monkeypatch.setattr(llm, "call", lambda *a, **k: _response(
        '## Corrections\n- "never use npm in this repo, always yarn" → Use yarn\n'))
    history = write_session(tmp_home / ".grok", cwd=REPO, session_id=GROK_A,
                            records=_no_edit_grok_records(),
                            prompt_times={0: "2026-10-16T12:58:06Z", 1: "2026-10-16T12:59:06Z"})

    kept = capture.corrections_only(history, "app", {})

    assert [k.quote for k in kept] == ["never use npm in this repo, always yarn"]
    (rec,) = list(friction_ledger.iter_records(friction_vault))
    assert rec.session_id == GROK_A


def _log_child(vault: Path, short_id: str) -> None:
    path = parents.log_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"short_id": short_id, "parent_session": "parent-1"}) + "\n")


def test_a_grok_child_is_found_by_its_short_id_and_its_neighbour_is_not(tmp_path: Path):
    _log_child(tmp_path, grok_paths.short_id(GROK_A))

    assert capture.is_dispatched_child(["hi"], GROK_A, tmp_path) is True
    assert capture.is_dispatched_child(["hi"], GROK_B, tmp_path) is False


def test_a_claude_child_is_still_found_by_its_first_eight(tmp_path: Path):
    _log_child(tmp_path, CLAUDE[:8])

    assert capture.is_dispatched_child(["hi"], CLAUDE, tmp_path) is True
