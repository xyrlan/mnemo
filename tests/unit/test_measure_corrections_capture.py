"""``tools/measure_corrections_capture.py`` over synthetic transcripts, briefings
and ledgers shaped like the real ones (#517)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_corrections_capture as tool  # noqa: E402

from mnemo.core import corrections  # noqa: E402
from mnemo.core.friction import capture, ledger  # noqa: E402
from mnemo.core.mcp import access_log  # noqa: E402

CWD = "/Users/you/github/app"


def _user(text, ts, *, cwd=CWD, entry="cli"):
    return {"type": "user", "timestamp": ts, "cwd": cwd, "entrypoint": entry,
            "message": {"role": "user", "content": text}}


def _edit(ts):
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Edit", "input": {}}]}}


def _briefing(*quotes):
    items = "".join(f'- "{q}" → rule\n' for q in quotes)
    return f"---\ntype: briefing\n---\n\n## TL;DR\nx\n\n## Corrections\n{items}"


@pytest.fixture
def telemetry_on(monkeypatch):
    monkeypatch.setattr(access_log, "_load_telemetry_config", lambda: (True, 1_048_576))


def test_who_counts_as_human():
    ok = [_user("fix the flaky test please", "2026-09-21T10:00:00Z")]
    assert tool.is_human(ok, "aaaaaaaa-1", set())
    assert not tool.is_human([], "aaaaaaaa-1", set())
    assert not tool.is_human([_user("Say OK", "2026-09-21T10:00:00Z", entry="sdk-cli")], "a", set())
    assert not tool.is_human([_user("hi there", "2026-09-21T10:00:00Z",
                                    cwd="/Users/you/.claude/jobs/abc/tmp/probe")], "a", set())
    assert not tool.is_human([_user("Work on issue #4 in this repo", "2026-09-21T10:00:00Z")], "a", set())
    assert not tool.is_human(ok, "aaaaaaaa-1", {"aaaaaaaa"})


def test_briefing_corrections_are_rechecked_against_todays_verify():
    ev = [_user("never use npm in this repo, always yarn", "2026-09-21T10:00:00Z"),
          _user("<bash-input>gh pr merge 1 --admin</bash-input>", "2026-09-21T10:01:00Z")]
    text = _briefing("never use npm in this repo", "gh pr merge 1 --admin", "made up by the model")
    assert tool.briefing_corrections(text, ev) == 1


def test_captured_rows_win_over_the_briefing_and_weeks_are_iso_utc():
    ev = [_user("never use npm in this repo, always yarn", "2026-09-20T23:30:00-03:00")]
    row = tool.session_row(ev, "s", parents=set(), briefing_text=_briefing("never use npm"),
                           captured_rows=3)
    # Sunday 23:30 at -03:00 is Monday 02:30 UTC: the next ISO week.
    assert row["week"] == "2026-W39" and row["date"] == "2026-09-21"
    assert (row["corrections"], row["source"], row["edits"], row["briefed"]) == (3, "ledger", False, True)


def test_weekly_counts_sessions_corrections_and_the_no_edit_share():
    rows = [
        {"week": "2026-W39", "edits": True, "briefed": True, "corrections": 2},
        {"week": "2026-W39", "edits": False, "briefed": False, "corrections": 1},
        {"week": "2026-W39", "edits": False, "briefed": False, "corrections": 0},
        {"week": "2026-W38", "edits": True, "briefed": False, "corrections": 0},
    ]
    weeks = tool.weekly(rows)
    assert [w["week"] for w in weeks] == ["2026-W38", "2026-W39"]
    w39 = weeks[1]
    assert (w39["human_sessions"], w39["briefed"], w39["with_correction"],
            w39["corrections"], w39["no_edit_corrections"]) == (3, 1, 2, 3, 1)
    total = tool.totals(weeks)
    assert total["human_sessions"] == 4 and total["corrections"] == 3
    out = tool.render(weeks)
    assert "2026-W39" in out and "33%" in out and out.splitlines()[-1].startswith("total")


def test_captured_counts_skip_backfilled_rows_and_dedupe_across_files(tmp_path, telemetry_on):
    a, b = tmp_path / "a", tmp_path / "b"
    live = ledger.FrictionRecord(session_id="s", project="p", quote="never use npm here",
                                 rule_text="r", capture="briefing", turn_index=0)
    for root in (a, b):
        ledger.record(root, live)
    ledger.record(a, ledger.FrictionRecord(session_id="t", project="p", quote="q" * 20,
                                           rule_text="r", backfilled=True))
    assert tool.captured_counts([ledger.ledger_path(a), ledger.ledger_path(b)]) == {"s": 1}


def _setup(tmp_path):
    projects, vault = tmp_path / "projects", tmp_path / "vault"
    proj = projects / "-Users-you-github-app"
    proj.mkdir(parents=True)
    sessions = {
        # edited and briefed, one correction in its briefing
        "edit0001-x": ([_user("never use npm in this repo, always yarn", "2026-09-22T10:00:00Z"),
                        _edit("2026-09-22T10:05:00Z")], _briefing("never use npm in this repo")),
        # no edit, no briefing: the session the old detector never read
        "noed0002-x": ([_user("stop adding emojis to commit messages", "2026-09-23T10:00:00Z")], None),
        # a dispatched child: not in the population
        "chld0003-x": ([_user("Work on issue #9 in this repo", "2026-09-23T11:00:00Z")], None),
    }
    for sid, (events, brief) in sessions.items():
        (proj / f"{sid}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n",
                                           encoding="utf-8")
        if brief:
            d = vault / "bots" / "app" / "briefings" / "sessions"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{sid}.md").write_text(brief, encoding="utf-8")
    return projects, vault


def test_main_before_counts_briefings_only(tmp_path, capsys):
    projects, vault = _setup(tmp_path)
    assert tool.main(["--projects", str(projects), "--vault", str(vault), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["total"]["human_sessions"] == 2
    assert out["total"]["corrections"] == 1 and out["total"]["no_edit_corrections"] == 0


def test_main_capture_into_runs_the_pass_on_no_edit_sessions_only(tmp_path, capsys, monkeypatch,
                                                                  telemetry_on):
    projects, vault = _setup(tmp_path)
    scratch = tmp_path / "scratch"
    ran = []

    def fake(path, agent, cfg, *, events):
        ran.append(path.stem)
        assert cfg["vaultRoot"] == str(scratch)
        item = corrections.Correction("stop adding emojis to commit messages", "No emojis")
        capture.record_session(scratch, events=events, session_id=path.stem, project=agent,
                               items=[item], capture=ledger.CAPTURE_CORRECTIONS_ONLY)
        return [item]
    monkeypatch.setattr(capture, "corrections_only", fake)
    assert tool.main(["--projects", str(projects), "--vault", str(vault),
                      "--capture-into", str(scratch), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert ran == ["noed0002-x"]
    assert out["total"]["corrections"] == 2 and out["total"]["no_edit_corrections"] == 1
    assert not ledger.ledger_path(vault).exists()  # the real vault is never written

    # a rerun reads the rows back instead of paying for them again
    ran.clear()
    tool.main(["--projects", str(projects), "--vault", str(vault),
               "--capture-into", str(scratch), "--json"])
    assert ran == [] and json.loads(capsys.readouterr().out)["total"]["corrections"] == 2
