"""``tools/measure_friction_screen.py`` over a synthetic ledger and transcript (#758)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_friction_screen as tool  # noqa: E402

SUMMARY = ("This session is being continued from a previous conversation that ran out "
           "of context.\n- the user said: Eu reviso e abro os PRs")


def _user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def _row(sid, quote, index, capture="briefing", ts="2026-10-20T10:00:00Z"):
    return {"session_id": sid, "quote": quote, "turn_index": index, "capture": capture, "ts": ts}


def _setup(tmp_path):
    projects = tmp_path / "projects" / "proj"
    projects.mkdir(parents=True)
    turns = [
        "never use npm in this repo, always yarn",
        "Pode mergear os dois por favor",
        "pode mergear, mas nunca sem rodar o CI",
        SUMMARY,
        "eai como esta indo a release?",
    ]
    (projects / "s1.jsonl").write_text(
        "\n".join(json.dumps(_user(t)) for t in turns) + "\n", encoding="utf-8")
    ledger = tmp_path / "vault" / ".mnemo" / "friction-ledger.jsonl"
    ledger.parent.mkdir(parents=True)
    rows = [
        _row("s1", "never use npm in this repo", 0),
        _row("s1", "Pode mergear os dois por favor", 1, capture="corrections_only"),
        _row("s1", "pode mergear, mas nunca", 2),
        _row("s1", "Eu reviso e abro os PRs", 3),
        _row("s1", "eai como esta indo a release?", 4),
        _row("gone", "Segue com o deploy agora", 0),
        {"session_id": "s1", "quote": "backfilled, not captured", "turn_index": 0},
    ]
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return tmp_path / "projects", ledger


def test_replay_judges_each_row_on_its_turn(tmp_path):
    projects, ledger = _setup(tmp_path)
    verdicts = tool.replay(tool.read_rows([ledger]), tool.transcripts(str(projects)))
    assert [(v["reason"], v["basis"]) for v in verdicts] == [
        ("", "turn"),
        ("go-ahead", "turn"),
        ("", "turn"),  # the whole turn holds a rule, though the quote opens with a go-ahead
        ("unverified", "turn"),  # only in a compaction summary
        ("question", "turn"),
        ("go-ahead", "quote_only"),  # transcript gone: the quote stands in
    ]
    s = tool.summary(verdicts)
    assert (s["rows"], s["kept"], s["quote_only"]) == (6, 2, 1)
    assert s["by_reason"] == {"kept": 2, "go-ahead": 2, "unverified": 1, "question": 1}
    assert s["by_capture"]["corrections_only"] == {"rows": 1, "kept": 0}


def test_since_and_until_bound_the_rows_by_date(tmp_path):
    ledger = tmp_path / "friction-ledger.jsonl"
    rows = [_row("s", "a quote long enough", 0, ts="2026-10-%02dT09:00:00Z" % d) for d in (13, 14, 26, 27)]
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    got = tool.read_rows([ledger], "2026-10-14", "2026-10-26")
    assert [r["ts"][:10] for r in got] == ["2026-10-14", "2026-10-26"]


def test_labelled_counts_what_the_screen_drops_of_each_kind():
    items = [{"id": "a", "turn": "never use npm, always yarn"},
             {"id": "b", "turn": "pode mergear"},
             {"id": "c", "turn": "como esta indo?"},
             {"id": "d", "turn": "unlabelled"}]
    gold = {"a": True, "b": False, "c": False}
    assert tool.labelled(items, gold) == {
        "corrections": 1, "corrections_dropped": 0, "others": 2, "others_dropped": 2}


def test_main_prints_the_counts(tmp_path, capsys):
    projects, _ = _setup(tmp_path)
    assert tool.main(["--vault", str(tmp_path / "vault"), "--projects", str(projects)]) == 0
    out = capsys.readouterr().out
    assert "ledger rows: 6  kept: 2  dropped: 4  (judged on the quote alone: 1)" in out
    assert "provenance: " in out
