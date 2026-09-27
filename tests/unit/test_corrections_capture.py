"""Session-end capture of corrections (#517): every verified correction becomes
one ledger row carrying its turn, its time, the child marker and the path that
found it — including sessions with no file edit, which get no briefing."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from mnemo.core import briefing as briefing_mod
from mnemo.core import corrections
from mnemo.core import llm as llm_mod
from mnemo.core.extract import prompts
from mnemo.core.extract.prompts.templates.briefing import (
    BRIEFING_SYSTEM_PROMPT,
    CORRECTIONS_DEFINITION,
)
from mnemo.core.friction import capture, ledger
from mnemo.core.mcp import access_log
from mnemo.core.transcript import user_turn_records, user_turns


def _user(text, ts, uuid=""):
    return {"type": "user", "timestamp": ts, "uuid": uuid, "entrypoint": "cli",
            "cwd": "/Users/you/github/app", "message": {"role": "user", "content": text}}


def _assistant(text, ts, tool=None):
    content = [{"type": "text", "text": text}]
    if tool:
        content.append({"type": "tool_use", "name": tool, "input": {}})
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": content}}


def _tool_result(ts):
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": [
        {"type": "tool_result", "content": "ok"}]}}


def _no_edit_events():
    """A question session: the user corrects the assistant, nothing is edited."""
    return [
        _user("how should I install the deps here?", "2026-09-20T23:58:00.000Z", "u0"),
        _assistant("Run npm install.", "2026-09-20T23:58:30.000Z", tool="Bash"),
        _tool_result("2026-09-20T23:58:40.000Z"),
        _user("<task-notification>done</task-notification>", "2026-09-20T23:59:00.000Z", "tn"),
        _user("never use npm in this repo, always yarn", "2026-09-21T00:01:00.000Z", "u1"),
        _assistant("Understood.", "2026-09-21T00:01:10.000Z"),
        _user("<bash-input>npm install</bash-input>", "2026-09-21T00:02:00.000Z", "u2"),
    ]


def _write(path: Path, events) -> Path:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


def _response(text):
    return llm_mod.LLMResponse(text=text, total_cost_usd=0.0, input_tokens=1,
                               output_tokens=1, api_key_source="none", raw={})


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: root)
    monkeypatch.setattr(access_log, "_load_telemetry_config", lambda: (True, 1_048_576))
    return root


# --- turns ------------------------------------------------------------------


def test_turn_records_align_with_user_turns_and_carry_time_and_uuid():
    ev = _no_edit_events()
    recs = user_turn_records(ev)
    assert [r.text for r in recs] == user_turns(ev)
    assert [r.index for r in recs] == [0, 1, 2]
    assert recs[1].timestamp == "2026-09-21T00:01:00.000Z" and recs[1].uuid == "u1"


def test_locate_returns_the_first_reaction_turn_and_skips_brief_and_shell():
    turns = [
        "Work on issue #9 in this repo: never use npm in this repo, always yarn",
        "<bash-input>never use npm in this repo, always yarn</bash-input>",
        "no. never use npm in this repo, always yarn",
        "I said: never use npm in this repo, always yarn",
    ]
    assert corrections.locate("never use npm in this repo, always yarn", turns) == 2
    assert corrections.reaction_indexes(turns) == [2, 3]
    assert corrections.locate("something nobody typed at all", turns) is None


def test_a_humans_first_turn_is_in_scope():
    assert corrections.locate("never use npm in this repo", ["never use npm in this repo"]) == 0


def test_exchanges_pair_each_turn_with_the_assistant_text_it_answered():
    pairs = capture.exchanges(_no_edit_events())
    assert [t for _, t in pairs] == user_turns(_no_edit_events())
    assert pairs[0][0] == ""
    assert pairs[1][0] == "Run npm install."


def test_child_is_read_from_the_brief_or_the_dispatch_log(tmp_path):
    assert capture.is_dispatched_child(["Work on issue #5 in this repo"], "abcdef12-x", None)
    assert not capture.is_dispatched_child(["fix the bug"], "abcdef12-x", tmp_path)
    (tmp_path / ".mnemo").mkdir()
    (tmp_path / ".mnemo" / "dispatch-parents.jsonl").write_text(
        json.dumps({"short_id": "abcdef12", "parent_session": "p"}) + "\n", encoding="utf-8")
    assert capture.is_dispatched_child(["fix the bug"], "abcdef12-x", tmp_path)


# --- the row ----------------------------------------------------------------


def test_record_session_writes_turn_time_child_and_path(vault):
    items = [corrections.Correction("never use npm in this repo", "Use yarn, never npm")]
    n = capture.record_session(
        vault, events=_no_edit_events(), session_id="s1", project="app",
        items=items, capture=ledger.CAPTURE_CORRECTIONS_ONLY,
    )
    assert n == 1
    (rec,) = list(ledger.iter_records(vault))
    assert (rec.session_id, rec.project, rec.turn_index) == ("s1", "app", 1)
    assert rec.turn_ts == "2026-09-21T00:01:00.000Z" and rec.turn_uuid == "u1"
    assert rec.capture == "corrections_only" and rec.child is False
    assert rec.entrypoint == "cli" and not rec.backfilled
    row = json.loads(ledger.ledger_path(vault).read_text(encoding="utf-8"))
    assert row["turn_index"] == 1 and row["capture"] == "corrections_only"


def test_rows_written_any_other_way_keep_their_old_shape(vault):
    ledger.record(vault, ledger.FrictionRecord(session_id="s", project="p", quote="q" * 20,
                                               rule_text="r", backfilled=True))
    row = json.loads(ledger.ledger_path(vault).read_text(encoding="utf-8"))
    assert "capture" not in row and "turn_index" not in row


def test_two_corrections_in_one_turn_are_both_kept(vault):
    ev = [_user("never use npm here, and never force-push to main", "2026-09-21T00:00:00Z")]
    items = [corrections.Correction("never use npm here", "No npm"),
             corrections.Correction("never force-push to main", "No force-push")]
    assert capture.record_session(vault, events=ev, session_id="s", project="p",
                                  items=items, capture=ledger.CAPTURE_BRIEFING) == 2


def test_a_second_pass_over_a_resumed_session_does_not_recount_a_turn(vault):
    ev = _no_edit_events()
    first = [corrections.Correction("never use npm in this repo", "No npm")]
    reworded = [corrections.Correction("always yarn", "Use yarn")]
    capture.record_session(vault, events=ev, session_id="s", project="p",
                           items=first, capture=ledger.CAPTURE_BRIEFING)
    assert capture.record_session(vault, events=ev, session_id="s", project="p",
                                  items=reworded, capture=ledger.CAPTURE_BRIEFING) == 0
    assert len(list(ledger.iter_records(vault))) == 1


def test_an_unlocatable_quote_is_not_recorded(vault):
    items = [corrections.Correction("npm install", "x")]  # only in a ! turn
    assert capture.record_session(vault, events=_no_edit_events(), session_id="s",
                                  project="p", items=items, capture="briefing") == 0


def test_secrets_are_redacted_in_the_row(vault):
    key = "sk-ant-api03-" + "A" * 40
    ev = [_user(f"never paste {key} into the config file", "2026-09-21T00:00:00Z")]
    items = [corrections.Correction(f"never paste {key} into the config", "Never commit keys")]
    capture.record_session(vault, events=ev, session_id="s", project="p",
                           items=items, capture="briefing")
    assert key not in ledger.ledger_path(vault).read_text(encoding="utf-8")


# --- the prompt -------------------------------------------------------------


def test_briefing_and_corrections_only_share_one_definition():
    assert CORRECTIONS_DEFINITION in BRIEFING_SYSTEM_PROMPT
    assert CORRECTIONS_DEFINITION in prompts.CORRECTIONS_SYSTEM_PROMPT


def test_corrections_prompt_numbers_turns_like_the_briefing_and_never_as_assistant():
    text = prompts.build_corrections_prompt(capture.exchanges(_no_edit_events()))
    assert "[1] how should I install the deps here?" in text
    assert "[2] never use npm in this repo, always yarn" in text
    assert "(assistant, before turn 2: Run npm install.)" in text
    assert not any(line.startswith("[") and "Run npm" in line for line in text.splitlines())


def test_corrections_prompt_keeps_the_tail_of_a_long_reply():
    text = prompts.build_corrections_prompt([("x" * 900 + " END", "no, stop that please now")])
    assert "END)" in text and "x" * 700 not in text


# --- the two paths ----------------------------------------------------------


def test_no_edit_session_gets_corrections_only_and_no_briefing(vault, tmp_path, monkeypatch):
    seen = {}

    def fake(prompt, *, system, model, timeout):
        seen["system"] = system
        return _response('## Corrections\n- "never use npm in this repo, always yarn" → Use yarn\n'
                         '- "always pnpm please" → Use pnpm\n')
    monkeypatch.setattr(llm_mod, "call", fake)
    jsonl = _write(tmp_path / "sess-noedit.jsonl", _no_edit_events())
    assert briefing_mod.generate_session_briefing(jsonl, "app", {}, record_corrections=True) is None

    kept = capture.corrections_only(jsonl, "app", {})
    assert [k.quote for k in kept] == ["never use npm in this repo, always yarn"]
    assert seen["system"] == prompts.CORRECTIONS_SYSTEM_PROMPT
    (rec,) = list(ledger.iter_records(vault))
    assert rec.capture == ledger.CAPTURE_CORRECTIONS_ONLY and rec.turn_index == 1
    assert not (vault / "bots").exists()


def test_a_session_with_nothing_quotable_costs_no_call(vault, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no LLM call expected")
    monkeypatch.setattr(llm_mod, "call", boom)
    ev = [_user("Work on issue #3 in this repo: fix it", "2026-09-21T00:00:00Z"),
          _user("<bash-input>ls</bash-input>", "2026-09-21T00:01:00Z"),
          _user("ok", "2026-09-21T00:02:00Z")]
    assert capture.corrections_only(_write(tmp_path / "c.jsonl", ev), "app", {}) == []


def test_a_briefed_session_records_its_corrections_when_asked(vault, tmp_path, monkeypatch):
    body = ("## TL;DR\nx\n\n## Corrections\n"
            '- "never use npm in this repo, always yarn" → Use yarn\n')
    monkeypatch.setattr(llm_mod, "call", lambda *a, **k: _response(body))
    ev = _no_edit_events() + [_assistant("editing", "2026-09-21T00:03:00Z", tool="Edit")]
    jsonl = _write(tmp_path / "sess-edit.jsonl", ev)

    briefing_mod.generate_session_briefing(jsonl, "app", {})
    assert list(ledger.iter_records(vault)) == []  # learn, backfill: not live

    out = briefing_mod.generate_session_briefing(jsonl, "app", {}, record_corrections=True)
    (rec,) = list(ledger.iter_records(vault))
    assert rec.capture == ledger.CAPTURE_BRIEFING and rec.turn_index == 1
    assert rec.briefing == out.relative_to(vault).as_posix()


def test_a_dispatched_child_is_recorded_and_marked(vault, tmp_path, monkeypatch):
    monkeypatch.setattr(llm_mod, "call", lambda *a, **k: _response(
        '## Corrections\n- "do not touch the lockfile again" → Leave the lockfile alone\n'))
    ev = [_user("Work on issue #7 in this repo: bump deps", "2026-09-21T00:00:00Z"),
          _assistant("I'll regenerate the lockfile.", "2026-09-21T00:00:30Z"),
          _user("do not touch the lockfile again", "2026-09-21T00:01:00Z", "c1")]
    capture.corrections_only(_write(tmp_path / "child.jsonl", ev), "app", {})
    (rec,) = list(ledger.iter_records(vault))
    assert rec.child is True and rec.turn_index == 1


def test_the_hidden_cli_runs_corrections_only_when_there_was_no_edit(vault, tmp_path, monkeypatch):
    from mnemo.cli.commands.briefing import cmd_briefing

    calls = []
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {})
    monkeypatch.setattr(capture, "corrections_only",
                        lambda path, agent, cfg: calls.append((path.name, agent)) or [])
    jsonl = _write(tmp_path / "s.jsonl", _no_edit_events())
    args = argparse.Namespace(prune=False, dry_run=False, jsonl_path=str(jsonl), agent="app")
    assert cmd_briefing(args) == 0
    assert calls == [("s.jsonl", "app")]
