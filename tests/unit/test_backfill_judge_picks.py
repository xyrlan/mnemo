"""``tools/backfill_judge_picks.py`` over a temp vault and hand-built
transcripts whose answers are known by construction (#619)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import backfill_judge_picks as tool  # noqa: E402

from mnemo.core.reflex import picks  # noqa: E402


def _log(sid, ts, scores, status="ok", project="app", h="h1"):
    return {"session_id": sid, "ts": ts, "project": project, "prompt_hash": h,
            "judge": {"status": status, "scores": scores}}


def _att(ts, text, cwd="/work/app"):
    return json.dumps({"type": "attachment", "timestamp": ts, "cwd": cwd, "isSidechain": False,
                       "attachment": {"type": "hook_additional_context", "hookName": "UserPromptSubmit",
                                      "content": [text]}})


BLOCK = "mnemo reflex context:\n• [[a]]:\nbody naming [[other]]\n• [[b]]:\nbody"


def test_log_rows_one_per_judged_prompt_with_picks_at_inject_at():
    rows = [_log("x", "2026-10-01T00:00:00Z", [["a", 0.4], ["b", 0.39]]),
            _log("x", "2026-10-01T00:00:00Z", [["a", 0.4], ["b", 0.39]]),          # the archive's copy
            _log("x", "2026-10-01T00:01:00Z", [["b", 0.1]], h="h2"),               # judged, none picked
            _log("y", "2026-10-01T00:02:00Z", [["a", 0.9]], status="timeout"),     # gates decided
            {"session_id": "z", "ts": "2026-10-01T00:03:00Z"}]                     # judge off
    assert tool.log_rows(rows) == [
        {"ts": "2026-10-01T00:00:00Z", "session_id": "x", "project": "app", "picks": ["a"]},
        {"ts": "2026-10-01T00:01:00Z", "session_id": "x", "project": "app", "picks": []}]


def test_transcript_rows_one_per_reflex_block_in_the_window():
    since = tool.mrc.epoch("2026-09-20T00:00:00Z")
    until = tool.mrc.epoch("2026-09-25T00:00:00Z")
    lines = [_att("2026-09-19T00:00:00Z", BLOCK), _att("2026-09-21T00:00:05Z", BLOCK),
             _att("2026-09-22T00:00:00Z", "mnemo://v1 project=app"),
             _att("2026-09-26T00:00:00Z", BLOCK)]                                  # the log has it
    got = tool.transcript_rows(lines, "s", since, until, lambda cwd: "proj:" + cwd)
    assert got == [{"ts": "2026-09-21T00:00:05Z", "session_id": "s", "project": "proj:/work/app",
                    "picks": ["a", "b"]}]


def _vault(tmp_path):
    vault, projects = tmp_path / "vault", tmp_path / "projects"
    (vault / ".mnemo").mkdir(parents=True)
    (projects / "-work-app").mkdir(parents=True)
    rows = [_log("new", "2026-10-01T00:00:00Z", [["c", 0.8]])]
    (vault / ".mnemo" / "reflex-log.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (projects / "-work-app" / "old.jsonl").write_text(
        "\n".join([_att("2026-09-21T00:00:00Z", BLOCK, cwd=str(tmp_path)),
                   _att("2026-10-02T00:00:00Z", BLOCK, cwd=str(tmp_path))]) + "\n", encoding="utf-8")
    return vault, projects


def test_backfill_writes_marked_rows_once(tmp_path, capsys):
    vault, projects = _vault(tmp_path)
    args = ["--vault", str(vault), "--projects", str(projects)]

    assert tool.main(args + ["--dry-run"]) == 0
    assert not (vault / ".mnemo" / picks.LEDGER_NAME).exists()

    assert tool.main(args) == 0
    rows = [json.loads(x) for x in (vault / ".mnemo" / picks.LEDGER_NAME).read_text(encoding="utf-8").splitlines()]
    assert [(r["session_id"], r["picks"]) for r in rows] == [("old", ["a", "b"]), ("new", ["c"])]
    assert all(r["backfilled"] for r in rows)
    ledger = picks.load(vault)
    assert ledger.since() == tool.mrc.epoch(tool.JUDGE_LIVE)
    assert ledger.picks_before(None, float("inf")) == {"a": 1, "b": 1, "c": 1}

    capsys.readouterr()
    assert tool.main(args) == 1
    assert "refused" in capsys.readouterr().err
    assert len((vault / ".mnemo" / picks.LEDGER_NAME).read_text(encoding="utf-8").splitlines()) == 2


def test_backfill_leaves_the_hooks_own_rows_alone(tmp_path):
    vault, projects = _vault(tmp_path)
    ts = tool.mrc.epoch("2026-10-01T00:00:00Z")
    picks.record(vault, session_id="new", project="app", picks=["c"], ts=ts)
    assert tool.main(["--vault", str(vault), "--projects", str(projects)]) == 0
    rows = [json.loads(x) for x in (vault / ".mnemo" / picks.LEDGER_NAME).read_text(encoding="utf-8").splitlines()]
    assert [(r["session_id"], bool(r.get("backfilled"))) for r in rows] == [("new", False), ("old", True)]


def test_a_transcript_row_takes_the_project_the_log_gave_its_session(tmp_path):
    vault, projects = _vault(tmp_path)
    (projects / "-work-app" / "new.jsonl").write_text(
        _att("2026-09-22T00:00:00Z", BLOCK, cwd="/gone/worktree") + "\n", encoding="utf-8")
    got = tool.collect(vault, projects)
    assert {r["session_id"]: r["project"] for r in got["rows"]}["new"] == "app"


def test_size_compacts_a_copy_and_leaves_the_ledger_alone(tmp_path, capsys):
    vault = tmp_path / "vault"
    for day in range(1, 21):
        for n in range(3):
            picks.record(vault, session_id="s%d-%d" % (day, n), project="app", picks=["a", "r%d" % n],
                         ts=tool.mrc.epoch("2026-09-%02dT12:00:00Z" % day))
    before = (vault / ".mnemo" / picks.LEDGER_NAME).read_bytes()
    r = tool.size_report(vault)
    assert r["rows"] == 60 and r["bytes"] == len(before)
    assert 0 < r["raw_after"] < r["bytes"] and r["summary"] > 0
    assert r["summary_days"] == 13 and r["totals_kept"] is True       # days 1..13 rolled, 14..20 raw
    assert tool.main(["--vault", str(vault), "--size"]) == 0
    assert "every rule's total kept: True" in capsys.readouterr().out
    assert (vault / ".mnemo" / picks.LEDGER_NAME).read_bytes() == before
    assert not (vault / ".mnemo" / picks.SUMMARY_NAME).exists()
