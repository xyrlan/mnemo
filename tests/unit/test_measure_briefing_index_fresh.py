"""``tools/measure_briefing_index_fresh.py`` over synthetic transcripts, briefings and raters (#551)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_briefing_index_fresh as tool  # noqa: E402

from mnemo.core import briefing_index as bix  # noqa: E402
from mnemo.hooks import session_start as ss  # noqa: E402

from tests.unit.test_measure_briefing_value import CWD, _agent, _hook, _resp, _user  # noqa: E402

R1, R2 = tool.RATERS
HEAD = "mnemo://v1 project=app\nlocal: [git]"
DAY = 86400


def _epoch(iso: str) -> float:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


START_AT = "2026-10-01T10:00:00Z"
EXPORT = "# Briefing\n\n## TL;DR\n\nThe csv exporter drops the header row; fix next.\n\n## What I did\n\n- looked"
LOGIN = "# Briefing\n\n## TL;DR\n\nWe shipped the login page.\n\n## State\n\nThe login page is done."


def _index(entries):
    block, _ = bix.index_block(entries, ss.ENVELOPE_MAX_BYTES - len(HEAD.encode()))
    return block


INDEX = _index([({"date": "2026-09-30"}, "We shipped the login page."),
                ({"date": "2026-09-29"}, "The csv exporter drops the header row; fix next.")])


def _session(start_text, source="startup", model="claude-opus-5-5", at=START_AT):
    t = _epoch(at)

    def ts(s):
        return datetime.fromtimestamp(t + s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [
        _hook("SessionStart:" + source, start_text),
        {"type": "attachment", "attachment": {"type": "instructions", "files": [
            {"path": CWD + "/CLAUDE.md", "type": "Project", "content": "Use yarn."}]}},
        _user("carry on with the export bug", ts(1), uuid="p0"),
        _agent("Looking at the exporter.", ts(5), model=model),
        _user("the csv export still drops the header row", ts(60), uuid="p1"),
        _agent("Fixed the header row; PR #12 open.", ts(120), model=model),
    ]


# --- the recorded envelope -------------------------------------------------------------------

def test_split_index_takes_the_block_out_wherever_it_sits():
    assert tool.split_index(HEAD + INDEX) == (HEAD, INDEX)
    assert tool.split_index(INDEX.lstrip("\n")) == ("", "\n\n" + INDEX.lstrip("\n"))
    learned = "[mnemo learned]\n• x\n[/mnemo learned]"
    assert tool.split_index(HEAD + INDEX + "\n\n" + learned) == (HEAD + "\n\n" + learned, INDEX)
    assert tool.split_index(HEAD) == (HEAD, "")


def test_with_slot_is_the_hooks_assembly():
    assert tool.with_slot(HEAD, INDEX) == HEAD + INDEX
    assert tool.with_slot("", INDEX) == INDEX.lstrip("\n")


def test_envelope_parts_finds_the_host_and_a_stray_last_briefing():
    parts = tool.envelope_parts([HEAD, HEAD + INDEX])
    assert (parts["heads"], parts["host"], parts["index"], parts["last"]) == ([HEAD, HEAD], 1, INDEX, False)
    parts = tool.envelope_parts([HEAD + INDEX, "[last-briefing session=x]\nb\n[/last-briefing]"])
    assert parts["last"] is True and parts["host"] == 0


def test_pool_is_the_briefings_written_before_the_session_newest_by_mtime():
    bs = [{"id": "a", "mtime": 1.0}, {"id": "b", "mtime": 3.0}, {"id": "later", "mtime": 9.0},
          {"id": "self", "mtime": 2.0}, {"id": "c", "mtime": 2.0}]
    assert [b["id"] for b in tool.pool_at(bs, "self", 5.0)] == ["b", "c", "a"]


def test_the_rebuilt_index_is_the_hooks():
    pool = [{"id": "b1", "date": "2026-09-30", "body": LOGIN}, {"id": "b2", "date": "2026-09-29", "body": EXPORT}]
    assert tool.rebuilt_index(pool, HEAD) == INDEX


# --- the verdicts ----------------------------------------------------------------------------

def _st(h, lo, hi):
    return {"n": 100, "h": h, "h_ci": [lo, hi]}


def test_the_decision_is_the_pre_registered_one():
    assert tool.decision(_st(0.1, 0.01, 0.2), 0.5).startswith("confirmed")
    assert tool.decision(_st(-0.1, -0.2, -0.01), 0.5).startswith("reversed")
    assert "revert" in tool.decision(_st(-0.1, -0.2, -0.01), 0.5)
    assert tool.decision(_st(0.05, -0.05, 0.15), 0.5).startswith("inconclusive")
    assert tool.decision(_st(0.3, 0.1, 0.5), 0.3) == "no verdict (ruler too weak)"
    assert tool.decision({"n": 0}, None) == "no estimate yet"


def test_due_needs_both_the_days_and_the_units():
    live = _epoch("2026-10-01T00:00:00Z")
    early = tool.due(live + 13 * DAY, live, 150)
    assert (early["date_ok"], early["units_ok"], early["due"]) == (False, True, False)
    few = tool.due(live + 20 * DAY, live, 99)
    assert (few["date_ok"], few["units_ok"], few["due"]) == (True, False, False)
    assert tool.due(live + 14 * DAY, live, 100)["due"] is True
    assert tool.due(live, None, 0)["due"] is False
    # 50 units in 10 days: 100 by day 20, later than day 14
    assert tool.projected(live + 10 * DAY, live, 50) == "2026-10-21T00:00:00Z"
    assert tool.projected(live + 10 * DAY, live, 0) is None


# --- end to end ------------------------------------------------------------------------------

def _setup(tmp_path):
    vault = tmp_path / "vault"
    d = vault / "bots" / "app" / "briefings" / "sessions"
    d.mkdir(parents=True)
    before = _epoch(START_AT) - DAY
    for sid, date, body, at in (("b-export", "2026-09-29", EXPORT, before - 3600),
                                ("b-login", "2026-09-30", LOGIN, before)):
        p = d / (sid + ".md")
        p.write_text("---\ntype: briefing\nsession_id: %s\ndate: %s\nduration_minutes: 9\n---\n\n%s\n"
                     % (sid, date, body), encoding="utf-8")
        os.utime(p, (at, at))
    projects = tmp_path / "projects" / "app"
    projects.mkdir(parents=True)
    sessions = {
        "s1": _session(HEAD + INDEX),
        "s2": _session(HEAD + INDEX, source="clear", model="claude-fable-5-1", at="2026-10-01T12:00:00Z"),
        "s3": _session(HEAD + INDEX, source="resume", at="2026-10-01T13:00:00Z"),  # not a cold start
        "s4": _session(HEAD, at="2026-10-01T14:00:00Z"),  # no index: not fresh
        "s5": _session(HEAD + INDEX, at="2026-10-20T09:00:00Z"),  # still settling
    }
    for sid, events in sessions.items():
        (projects / (sid + ".jsonl")).write_text("\n".join(json.dumps(e) for e in events) + "\n",
                                                 encoding="utf-8")
    return ["--vault", str(vault), "--projects", str(tmp_path / "projects"),
            "--claude-home", str(tmp_path / "claude"), "--out", str(tmp_path / "out"),
            "--pause", "0", "--workers", "1"]


NOW = _epoch("2026-10-20T12:00:00Z")


def test_a_dry_run_counts_fresh_sessions_and_refuses_to_send_early(tmp_path, capsys, monkeypatch):
    from mnemo.core import llm

    base = _setup(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm, "resolve", lambda cfg: pytest.fail("no model before it is due"))
    assert tool.run(base + ["--json"], now=NOW) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["due"]["live"] == "2026-10-01T10:00:01Z"  # its first event
    assert data["counts"]["carried"] == 4
    assert data["counts"]["units"] == 2
    assert data["counts"]["left_out"]["not_startup"] == 1
    assert data["counts"]["left_out"]["settling"] == 1
    assert data["counts"]["rebuilt"] == 2
    assert data["due"]["date_ok"] and not data["due"]["units_ok"] and not data["due"]["due"]
    assert data["pending"]["answers"] == 2 * 3 * 2

    assert tool.run(base + ["--send"], now=NOW) == 0
    captured = capsys.readouterr()
    assert "refusing to send: 2 of 100 units" in captured.err
    assert "NOT DUE" in captured.out

    arms = json.loads((tmp_path / "out" / "arms.json").read_text(encoding="utf-8"))
    a = arms["s1"]
    assert a["last"] == "b-login" and a["ids"] == ["b-login", "b-export"] and a["entries"] == 2
    p = a["prompts"]
    assert "[recent-briefings count=2" in p["index"] and "[last-briefing" not in p["index"]
    assert "[last-briefing session=b-login date=2026-09-30 duration_minutes=9]" in p["last"]
    assert "The login page is done." in p["last"] and "[recent-briefings" not in p["last"]
    assert "login" not in p["none"] and "[recent-briefings" not in p["none"]
    for arm in tool.ARMS:
        assert HEAD in p[arm] and "carry on with the export bug" in p[arm]


def test_forced_run_answers_judges_and_reports(tmp_path, capsys, monkeypatch):
    from mnemo.core import llm

    base = _setup(tmp_path)
    monkeypatch.chdir(tmp_path)
    asked = []

    def provider(prompt, *, system, model, timeout):
        asked.append((model, system))
        if system == tool.rl.ARM_SYSTEM:
            if "[last-briefing" in prompt:
                return _resp("The login page is done, so I will polish it.")
            if "[recent-briefings" in prompt and model == "claude-opus-5-5":
                return _resp("Fixing the csv header row next.")
            return _resp("Which export bug do you mean?")
        assert system == tool.JUDGE_SYSTEM
        assert "b-login" not in prompt and "recent-briefings" not in prompt
        replies = prompt.split("## Reply 1")[1].split("## What the session")[0].split("## Reply 2")
        score = [("header" in r) - ("login" in r) for r in replies]
        better = "1" if score[0] > score[1] else "2" if score[1] > score[0] else "tie"
        return _resp(json.dumps({"better": better, "why": "w",
                                 "wrong_state": {"1": "login" in replies[0], "2": "login" in replies[1]}}))
    monkeypatch.setattr(llm, "resolve", lambda cfg: provider)
    assert tool.run(base + ["--send", "--force", "--json"], now=NOW) == 0
    data = json.loads(capsys.readouterr().out)
    systems = [s for _, s in asked]
    assert systems.count(tool.rl.ARM_SYSTEM) == 2 * 3 * 2
    assert systems.count(tool.JUDGE_SYSTEM) == 2 * 2 * 2 * 2
    assert {m for m, s in asked if s == tool.rl.ARM_SYSTEM} == {"claude-opus-5-5", "claude-fable-5-1"}
    both = data["results"]["both"]
    # s1 (Opus): the index finds the work, beats both; s2 (Fable): it only
    # asks, ties with none and still beats a last briefing that assumed wrong
    assert both["last"]["n"] == 2 and both["last"]["h"] == 1.0 and both["last"]["wrong_control"] == 1.0
    assert both["none"]["n"] == 2 and both["none"]["h"] == 0.5 and both["none"]["wrong_arm"] == 0.0
    assert data["kappa"]["kappa"] == 1.0
    assert data["verdict"]["decision"].startswith("confirmed")
    assert data["verdict"]["index"] == "inconclusive"

    asked.clear()
    assert tool.run(base + ["--send", "--force"], now=NOW) == 0
    out = capsys.readouterr().out
    assert asked == []
    assert "index vs last: confirmed" in out and "index vs none: inconclusive" in out


def test_live_can_be_pinned(tmp_path, capsys):
    base = _setup(tmp_path)
    assert tool.run(base + ["--json", "--live", "2026-10-01T11:00:00Z"], now=NOW) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["live_from"] == "--live"
    assert data["counts"]["units"] == 1  # s1 started before it
