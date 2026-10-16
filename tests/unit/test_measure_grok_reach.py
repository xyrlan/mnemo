"""tools/measure_grok_reach.py over synthetic Grok histories.

The record shapes are those of the ``chat_history.jsonl`` grok 1.0.60 wrote
for probe sessions on 2026-10-16: a ``system`` record, the first ``user``
record carrying the rules (``always_applied_workspace_rule``), the prompt as
a ``user`` record with ``prompt_index``, and a PreToolUse hook's context as a
``user`` record with ``synthetic_reason: system_reminder`` after the tool's
``tool_result``.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools import measure_grok_reach as M  # noqa: E402

RULE = "/r/.grok/rules/mnemo-briefing.md"


def _user(text: str, **extra) -> dict:
    return dict({"type": "user", "content": [{"type": "text", "text": text}]}, **extra)


def _rules(with_briefing: bool) -> dict:
    rule = ('<always_applied_workspace_rule name="%s">\n'
            "[mnemo:briefing via=rules-file project=p]\nmnemo://v1\n"
            "</always_applied_workspace_rule>" % RULE) if with_briefing else ""
    return _user("<user_info>\nOS Version: macos\n</user_info>\n\n<rules>\n%s\n</rules>" % rule)


def _reminder(text: str) -> dict:
    return _user("<system-reminder>\nContext from PreToolUse hook "
                 "'global/settings:pre_tool_use[0].hooks[0]':\n%s\n</system-reminder>" % text,
                 synthetic_reason="system_reminder")


def _turn(n: int, *, tool: bool, context: str = "") -> List[dict]:
    out = [_user("<user_query>\nprompt %d\n</user_query>" % n, prompt_index=n)]
    if tool:
        out.append({"type": "assistant", "content": "",
                    "tool_calls": [{"id": "c%d" % n, "name": "read_file", "arguments": "{}"}]})
        out.append({"type": "tool_result", "tool_call_id": "c%d" % n, "content": "1→x"})
        if context:
            out.append(_reminder(context))
    out.append({"type": "assistant", "content": "done %d" % n})
    return out


def _write(home: Path, sid: str, records: List[dict]) -> Path:
    folder = home / "sessions" / "%2Fr" / sid
    folder.mkdir(parents=True)
    path = folder / "chat_history.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records) + "not json\n", encoding="utf-8")
    return path


def test_a_session_briefed_by_file_with_context_on_its_tool_turns() -> None:
    records = [{"type": "system", "content": "You are Grok"}, _rules(True)]
    records += _turn(0, tool=True, context="[mnemo:prompt-context]\nmnemo reflex context:")
    records += _turn(1, tool=False)
    records += _turn(2, tool=True)
    report = M.session_report(records)
    assert report == {"turns": 3, "briefing_via_file": True, "briefing_via_tool_call": False,
                      "briefing_later": False, "turns_with_tool_call": 2,
                      "turns_with_context": 1}


def test_a_briefing_on_the_first_tool_call_counts_at_turn_one_only_there() -> None:
    first = [_rules(False)] + _turn(0, tool=True, context=(
        "[mnemo:briefing via=first-tool-call]\nmnemo://v1\n\n[mnemo:held-notices]\nchild done"))
    report = M.session_report(first)
    assert report["briefing_via_tool_call"] and report["turns_with_context"] == 1
    late = ([_rules(False)] + _turn(0, tool=False)
            + _turn(1, tool=True, context="[mnemo:briefing via=first-tool-call]\nx"))
    report = M.session_report(late)
    assert not report["briefing_via_tool_call"] and report["briefing_later"]


def test_text_in_the_users_own_words_is_not_counted() -> None:
    """Only injected records count: a prompt quoting a marker is the user's."""
    records = [_rules(False), _user("why does [mnemo:prompt-context] show up?", prompt_index=0)]
    assert M.session_report(records)["turns_with_context"] == 0
    assert M.session_report([_rules(False)]) == {"turns": 0}


def test_measure_counts_sessions_and_turns_and_filters_by_start(tmp_path: Path) -> None:
    home = tmp_path / "grok-home"
    # UUIDv7 ids: 0x019a... is 2025-10, 0x01a144... is 2026-10.
    _write(home, "01a144cd-aa48-7271-b671-114727e068a2",
           [_rules(True)] + _turn(0, tool=True, context="[mnemo:prompt-context]\nr")
           + _turn(1, tool=False))
    _write(home, "01a144ce-0000-7000-8000-000000000001", [_rules(False)] + _turn(0, tool=True))
    _write(home, "019a0000-0000-7000-8000-000000000002", [_rules(True)] + _turn(0, tool=True))
    _write(home, "01a144cf-0000-7000-8000-000000000003", [_rules(False)])  # no prompt yet
    report = M.measure([home], since=datetime(2026, 1, 1))
    assert report["histories"] == 3 and report["sessions"] == 2
    assert report["briefing"] == {"via_file": 1, "via_first_tool_call": 0, "at_turn_1": 1,
                                  "after_turn_1": 0}
    assert report["prompt_context"] == {"turns": 3, "turns_with_tool_call": 2,
                                        "turns_without_tool_call": 1, "turns_reached": 1}
    assert M.measure([home])["sessions"] == 3
    assert "briefing at turn 1: 1/2 (50%)" in M.render(report)


def test_main_prints_a_stamped_report(tmp_path: Path, capsys) -> None:
    home = tmp_path / "grok-home"
    _write(home, "01a144cd-aa48-7271-b671-114727e068a2", [_rules(True)] + _turn(0, tool=True))
    assert M.main(["--home", str(home), "--since", "2026-10-01", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["sessions"] == 1 and report["provenance"]["tool"] == "measure_grok_reach"
    assert M.main(["--home", str(tmp_path / "none")]) == 0
    assert capsys.readouterr().out.splitlines()[-1].startswith("provenance: ")
