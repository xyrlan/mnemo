"""The correction check (#524): a second look at every verified correction, on
both capture paths, that fails open and costs nothing when off or idle."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core import briefing as briefing_mod
from mnemo.core import config
from mnemo.core import correction_check as cc
from mnemo.core import corrections
from mnemo.core import llm as llm_mod
from mnemo.core.friction import capture, ledger
from mnemo.core.mcp import access_log

ON = {"extraction": {"correctionCheck": {"enabled": True, "model": "claude-opus-5-5"}}}


def _user(text, ts, uuid=""):
    return {"type": "user", "timestamp": ts, "uuid": uuid, "entrypoint": "cli",
            "cwd": "/Users/you/github/app", "message": {"role": "user", "content": text}}


def _assistant(text, ts, tool=None):
    content = [{"type": "text", "text": text}]
    if tool:
        content.append({"type": "tool_use", "name": tool, "input": {}})
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": content}}


def _events(edit=False):
    ev = [
        _user("add the retry helper please", "2026-09-21T00:00:00Z", "u0"),
        _assistant("I'll install it with npm install.", "2026-09-21T00:00:30Z"),
        _user("never use npm in this repo, always yarn", "2026-09-21T00:01:00Z", "u1"),
        _assistant("Done. Shall I open the PR and merge it?", "2026-09-21T00:02:00Z"),
        _user("sim, pode abrir o PR e mergear", "2026-09-21T00:03:00Z", "u2"),
    ]
    if edit:
        ev.append(_assistant("editing", "2026-09-21T00:04:00Z", tool="Edit"))
    return ev


SECTION = ('## Corrections\n'
           '- "never use npm in this repo, always yarn" → Use yarn\n'
           '- "sim, pode abrir o PR e mergear" → Always merge without asking\n')


def _write(path: Path, events) -> Path:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


def _response(text):
    return llm_mod.LLMResponse(text=text, total_cost_usd=0.02, input_tokens=10,
                               output_tokens=5, api_key_source="none", raw={})


def _verdict(*flags):
    return json.dumps({"labels": [{"id": str(n), "correction": f} for n, f in enumerate(flags, 1)]})


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: root)
    monkeypatch.setattr(access_log, "_load_telemetry_config", lambda: (True, 1_048_576))
    return root


def _errors(vault):
    from mnemo.core import errors

    return errors._log_path(vault).read_text(encoding="utf-8")


def _items():
    return [corrections.Correction("never use npm in this repo, always yarn", "Use yarn"),
            corrections.Correction("sim, pode abrir o PR e mergear", "Always merge")]


# --- prompt and parse -------------------------------------------------------------------

def test_prompt_numbers_items_and_shows_the_agent_tail_and_the_turn_head():
    text = cc.build_prompt([("a" * 2000 + "END", "no, use yarn"), ("", "ok " + "b" * 2000)])
    assert "### item 1\nAGENT:\n…" in text and "END" in text and "a" * 1501 not in text
    assert "### item 2\nAGENT:\n(no text)" in text
    assert "USER:\nok " in text and "b" * 1501 not in text


def test_verdicts_read_every_id_shape_models_echo_and_ignore_the_rest():
    text = ('```json\n{"labels": [{"id": "1", "correction": true}, {"id": 2, "correction": false},'
            ' {"id": "item 3", "correction": true}, {"id": "9", "correction": true},'
            ' {"id": "4", "correction": "yes"}, {"id": "abc", "correction": false}]}\n```')
    assert cc.parse_verdicts(text, 4) == {1: True, 2: False, 3: True}
    assert cc.parse_verdicts("no json here", 2) == {}
    assert cc.parse_verdicts('{"labels": "nope"}', 2) == {}


def test_it_is_off_by_default_and_on_by_config():
    assert cc.settings(config.DEFAULTS) == (False, "claude-opus-5-5")
    assert cc.settings({}) == (False, cc.DEFAULT_MODEL)
    assert cc.settings(ON) == (True, "claude-opus-5-5")


# --- the stage --------------------------------------------------------------------------

def test_off_or_nothing_to_check_costs_no_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no call expected")
    monkeypatch.setattr(llm_mod, "call", boom)
    pairs = capture.exchanges(_events())
    assert cc.check(_items(), pairs, {}) == (_items(), [])
    assert cc.check([], pairs, ON) == ([], [])


def test_an_item_answered_no_is_dropped_and_each_sees_its_own_turn(vault, monkeypatch):
    seen = {}

    def fake(prompt, *, system, model, timeout):
        seen.update(prompt=prompt, system=system, model=model)
        return _response(_verdict(True, False))
    monkeypatch.setattr(llm_mod, "call", fake)
    kept, dropped = cc.check(_items(), capture.exchanges(_events()), ON, vault_root=vault, agent="app")
    assert [k.quote for k in kept] == ["never use npm in this repo, always yarn"]
    assert [d.quote for d in dropped] == ["sim, pode abrir o PR e mergear"]
    assert seen["system"] == cc.SYSTEM_PROMPT and seen["model"] == "claude-opus-5-5"
    assert "AGENT:\nI'll install it with npm install.\n\nUSER:\nnever use npm" in seen["prompt"]
    assert "AGENT:\nDone. Shall I open the PR and merge it?\n\nUSER:\nsim, pode" in seen["prompt"]
    rows = [json.loads(line) for line in
            (vault / ".mnemo" / "mcp-access-log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["purpose"] for r in rows] == ["correction_check"]


@pytest.mark.parametrize("reply", ["not json", _verdict(False)])
def test_an_unanswered_item_is_kept_and_logged(vault, monkeypatch, reply):
    monkeypatch.setattr(llm_mod, "call", lambda *a, **k: _response(reply))
    kept, dropped = cc.check(_items(), capture.exchanges(_events()), ON, vault_root=vault)
    assert [k.quote for k in kept] == [i.quote for i in _items()][len(dropped):]
    assert "correction_check.unanswered" in _errors(vault)


def test_a_failed_call_keeps_everything_and_never_raises(vault, monkeypatch):
    def boom(*a, **k):
        raise llm_mod.LLMSubprocessError("rate limited")
    monkeypatch.setattr(llm_mod, "call", boom)
    assert cc.check(_items(), capture.exchanges(_events()), ON, vault_root=vault) == (_items(), [])
    assert "correction_check.call" in _errors(vault)


# --- both capture paths -----------------------------------------------------------------

def _two_calls(first, verdict):
    calls = []

    def fake(prompt, *, system, model, timeout):
        calls.append(system)
        return _response(first if len(calls) == 1 else verdict)
    return fake, calls


def test_the_briefing_keeps_only_what_the_check_confirms(vault, tmp_path, monkeypatch):
    fake, calls = _two_calls("## TL;DR\nx\n\n" + SECTION, _verdict(True, False))
    monkeypatch.setattr(llm_mod, "call", fake)
    out = briefing_mod.generate_session_briefing(
        _write(tmp_path / "s-edit.jsonl", _events(edit=True)), "app", ON, record_corrections=True)
    text = out.read_text(encoding="utf-8")
    assert "never use npm in this repo" in text and "pode abrir o PR" not in text
    assert "corrections: 1" in text
    assert calls[1] == cc.SYSTEM_PROMPT
    assert [r.quote for r in ledger.iter_records(vault)] == ["never use npm in this repo, always yarn"]


def test_corrections_only_records_only_what_the_check_confirms(vault, tmp_path, monkeypatch):
    fake, calls = _two_calls(SECTION, _verdict(True, False))
    monkeypatch.setattr(llm_mod, "call", fake)
    kept = capture.corrections_only(_write(tmp_path / "s-noedit.jsonl", _events()), "app", ON)
    assert [k.quote for k in kept] == ["never use npm in this repo, always yarn"]
    assert [r.quote for r in ledger.iter_records(vault)] == ["never use npm in this repo, always yarn"]
    assert len(calls) == 2


def test_with_the_check_off_both_paths_make_one_call_as_before(vault, tmp_path, monkeypatch):
    fake, calls = _two_calls(SECTION, _verdict(False, False))
    monkeypatch.setattr(llm_mod, "call", fake)
    kept = capture.corrections_only(_write(tmp_path / "s.jsonl", _events()), "app", config.DEFAULTS)
    assert len(kept) == 2 and len(calls) == 1
