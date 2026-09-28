"""``tools/measure_correction_check.py`` over synthetic items, labels,
transcripts and gate-verified pages (#524)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_correction_check as tool  # noqa: E402

from mnemo.core import correction_check  # noqa: E402
from mnemo.core import llm  # noqa: E402

mrc = tool.mrc
OPUS, FABLE = mrc.RATERS

# Even ids are dev, odd ids are test.
DEV_A, DEV_B, DEV_C = "000000000002", "000000000004", "000000000006"
TEST_A, TEST_B = "000000000001", "000000000003"


def _item(iid, sid, turn, answered="I will use npm.", index=1, path="briefing"):
    return {"id": iid, "session_id": sid, "turn_index": index, "turn": turn,
            "answered": answered, "path": path, "quote": turn[:40]}


def _labels(opus, fable):
    return {mrc.column(OPUS, mrc.LABEL_SYSTEM): opus, mrc.column(FABLE, mrc.LABEL_SYSTEM): fable}


def _response(text):
    return llm.LLMResponse(text=text, total_cost_usd=0.02, input_tokens=100,
                           output_tokens=10, api_key_source="none", raw={})


def test_parts_are_the_id_parity():
    assert tool.part_of(DEV_A) == tool.DEV and tool.part_of(TEST_A) == tool.TEST


def test_truth_is_both_raters_and_needs_both_answers():
    items = [_item(DEV_A, "s", "x"), _item(DEV_B, "s", "y"), _item(DEV_C, "s", "z")]
    labels = _labels({DEV_A: True, DEV_B: True, DEV_C: True}, {DEV_A: True, DEV_B: False})
    assert tool.truth(items, labels, mrc.RATERS) == {DEV_A: True, DEV_B: False}


def test_confusion_and_rates_with_intervals():
    gold = {"a": True, "b": True, "c": False, "d": False}
    c = tool.confusion(gold, {"a": True, "b": False, "c": True, "d": False}, list(gold))
    assert c == {"tp": 1, "fp": 1, "fn": 1, "tn": 1}
    r = tool.rates(c)
    assert r["precision"] == 0.5 and r["recall"] == 0.5
    lo, hi = r["precision_ci95"]
    assert 0 < lo < 0.5 < hi < 1
    assert tool.rates({"tp": 0, "fp": 0, "fn": 0, "tn": 3})["precision"] is None


def test_score_runs_the_live_prompt_one_call_per_session_and_resumes():
    items = [_item(DEV_B, "s1", "sim pode mergear", index=4),
             _item(DEV_A, "s1", "never use npm, always yarn", index=2),
             _item(DEV_C, "s2", "abre o PR")]
    asked = []

    def ask(prompt):
        asked.append(prompt)
        if "never use npm" in prompt:  # s1, in turn order: DEV_A is item 1
            assert prompt.index("never use npm") < prompt.index("sim pode mergear")
            return _response('{"labels": [{"id": "1", "correction": true}, {"id": "2", "correction": false}]}')
        return _response("I cannot tell")

    rows = []
    got = tool.score(items, {}, ask, correction_check, on_answer=lambda g, r: rows.append(r))
    assert got == {DEV_A: True, DEV_B: False}
    assert len(asked) == 2 and "### item 1\nAGENT:\nI will use npm." in asked[0]
    assert [r["answered"] for r in rows] == [2, 0] and rows[0]["usd"] == 0.02
    # A rerun asks only the session that went unanswered.
    asked.clear()
    tool.score(items, got, ask, correction_check)
    assert len(asked) == 1 and "abre o PR" in asked[0]


def test_call_stats():
    s = tool.call_stats([{"secs": 2, "usd": 0.01, "in": 100}, {"secs": 4, "usd": 0.03, "in": 300}])
    assert s["calls"] == 2 and s["secs_median"] == 3 and abs(s["usd_per_call"] - 0.02) < 1e-9
    assert tool.call_stats([]) == {"calls": 0}


def _cache(tmp_path):
    d = tmp_path / "cache"
    d.mkdir()
    items = [_item(DEV_A, "s1", "never use npm in this repo, always yarn"),
             _item(TEST_A, "s2", "do it in a worktree next time"),
             _item(TEST_B, "s3", "pode mergear")]
    (d / "items.json").write_text(json.dumps(items), encoding="utf-8")
    (d / "labels.json").write_text(json.dumps(_labels(
        {DEV_A: True, TEST_A: True, TEST_B: True}, {DEV_A: True, TEST_A: True, TEST_B: False})), encoding="utf-8")
    return d


def _fake_provider(monkeypatch, reply):
    monkeypatch.setattr(llm, "call", lambda prompt, **k: _response(reply(prompt)))


def test_main_scores_a_part_reports_it_and_guards_the_test_half(tmp_path, monkeypatch, capsys):
    d = _cache(tmp_path)
    _fake_provider(monkeypatch, lambda p: '{"labels": [{"id": "1", "correction": %s}]}'
                   % ("false" if "pode mergear" in p else "true"))
    args = ["--dir", str(d), "--vault", str(tmp_path), "--pause", "0", "--model", "m-one"]

    assert tool.main(args + ["--score", "--part", "test"]) == 0
    out = capsys.readouterr().out
    assert "test half: 2 items, 1 real" in out
    assert "check m-one@" in out and "precision 100.0%" in out
    assert json.loads((d / tool.TEST_NAME).read_text(encoding="utf-8")) == [tool.column("m-one", correction_check.SYSTEM_PROMPT)]
    calls = [json.loads(line) for line in (d / tool.CALLS_NAME).read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 2 and {c["part"] for c in calls} == {"test"}

    # Same column again is a resume, not a second score; another column is refused.
    assert tool.main(args + ["--score", "--part", "test"]) == 0
    other = ["--dir", str(d), "--vault", str(tmp_path), "--pause", "0", "--model", "m-two"]
    assert tool.main(other + ["--score", "--part", "test"]) == 2
    assert "refusing" in capsys.readouterr().err
    assert tool.main(other + ["--score", "--part", "test", "--again"]) == 0
    assert len(json.loads((d / tool.TEST_NAME).read_text(encoding="utf-8"))) == 2
    # Dev is never guarded.
    assert tool.main(other + ["--score", "--part", "dev"]) == 0


# --- the gate-verified pages ------------------------------------------------------------

def _user(text, ts):
    return {"type": "user", "timestamp": ts, "cwd": "/Users/you/github/app",
            "message": {"role": "user", "content": text}}


def _agent(text, ts):
    return {"type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _page(vault, slug, quote, sid, *, confidence="verified", listed=True):
    source = "bots/app/briefings/sessions/%s.md" % sid
    path = vault / "shared" / "feedback" / (slug + ".md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nname: %s\ndescription: d\ntype: feedback\nstability: stable\nconfidence: %s\n"
        "sources:\n  - %s\nevidence:\n  quote: \"%s\"\n  source: %s\n---\nbody\n"
        % (slug, confidence, source if listed else "bots/app/briefings/sessions/other.md", quote, source),
        encoding="utf-8")


def _briefing(vault, sid, *quotes):
    path = vault / "bots" / "app" / "briefings" / "sessions" / (sid + ".md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntype: briefing\n---\n\n## Corrections\n"
                    + "".join('- "%s" → rule\n' % q for q in quotes), encoding="utf-8")


def _transcript(projects, sid, events):
    path = projects / "-Users-you-github-app" / (sid + ".jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def test_gate_pages_are_the_ones_the_evidence_gate_verifies_today(tmp_path):
    vault = tmp_path / "vault"
    _briefing(vault, "s1", "never use npm in this repo, always yarn")
    _page(vault, "use-yarn", "never use npm in this repo, always yarn", "s1")
    _page(vault, "label-only", "never use npm in this repo, always yarn", "s1", listed=False)
    _page(vault, "inferred", "never use npm in this repo, always yarn", "s1", confidence="inferred")
    got = tool.gate_pages(vault)
    assert [p["slug"] for p in got] == ["use-yarn"]
    assert tool.session_of_source(got[0]["source"]) == "s1"


def test_gate_items_reuse_a_label_only_when_the_raters_saw_that_quote(tmp_path):
    projects = tmp_path / "projects"
    long_turn = "sim pode abrir o PR e mergear " + "x" * 1600 + " e nunca use npm neste repositorio"
    _transcript(projects, "s1", [
        _agent("Shall I merge?", "2026-09-20T10:00:00Z"),
        _user(long_turn, "2026-09-20T10:01:00Z"),
        _agent("Using npm.", "2026-09-20T10:02:00Z"),
        _user("never use npm in this repo, always yarn", "2026-09-20T10:03:00Z"),
    ])
    known = [_item(DEV_A, "s1", mrc._head(long_turn, mrc.TURN_CHARS), index=0),
             _item(DEV_B, "s1", "never use npm in this repo, always yarn", index=1)]
    pages = [
        {"slug": "a", "quote": "never use npm in this repo", "source": "bots/app/b/s1.md"},
        {"slug": "b", "quote": "sim pode abrir o PR e mergear", "source": "bots/app/b/s1.md"},
        {"slug": "c", "quote": "e nunca use npm neste repositorio", "source": "bots/app/b/s1.md"},
        {"slug": "d", "quote": "a quote nobody typed at all", "source": "bots/app/b/s1.md"},
        {"slug": "e", "quote": "whatever was said then", "source": "bots/app/b/gone.md"},
    ]
    rows = {r["slug"]: r for r in tool.gate_items(pages, projects, known)}
    assert (rows["a"]["status"], rows["a"]["id"]) == ("labelled", DEV_B)
    assert (rows["b"]["status"], rows["b"]["id"]) == ("labelled", DEV_A)
    # Same turn, but past what the raters were shown: new, with a window on the quote.
    assert rows["c"]["status"] == "new" and rows["c"]["id"] not in (DEV_A, DEV_B)
    assert "nunca use npm" in rows["c"]["turn"] and rows["c"]["turn"].startswith("…")
    assert rows["c"]["answered"] == "Shall I merge?"
    assert rows["d"]["status"] == "not located"
    assert rows["e"]["status"] == "transcript gone"


def test_gate_counts():
    rows = [{"status": "labelled", "id": "1"}, {"status": "labelled", "id": "2"},
            {"status": "new", "id": "3"}, {"status": "new", "id": "4"},
            {"status": "transcript gone"}, {"status": "not located"}]
    answers = {"r1": {"1": False, "2": True, "3": True}, "r2": {"1": False, "2": True, "3": False}}
    assert tool.gate_counts(rows, answers) == {
        "pages": 6, "transcript gone": 1, "not located": 1, "unlabelled": 1,
        "both correction": 1, "both not": 1, "split": 1}


def test_main_gate_labels_unseen_quotes_and_touches_no_page(tmp_path, monkeypatch, capsys):
    vault, projects = tmp_path / "vault", tmp_path / "projects"
    _briefing(vault, "s1", "pode mergear o PR develop agora sem esperar o CI rodar")
    _page(vault, "merge-it", "pode mergear o PR develop agora sem esperar o CI rodar", "s1")
    before = (vault / "shared" / "feedback" / "merge-it.md").read_text(encoding="utf-8")
    _transcript(projects, "s1", [_agent("Merge?", "2026-09-20T10:00:00Z"),
                                 _user("pode mergear o PR develop agora sem esperar o CI rodar", "2026-09-20T10:01:00Z")])
    d = tmp_path / "cache"
    d.mkdir()
    (d / "items.json").write_text("[]", encoding="utf-8")
    (d / "labels.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mrc, "run_calls", lambda provider, model, timeout, calls, *a, **k: [
        on_reply('{"labels": [{"id": "%s", "correction": false}]}' % tool.mrc.item_id(
            "s1", 0, "pode mergear o PR develop agora sem esperar o CI rodar")) for _p, _s, on_reply in calls])
    args = ["--dir", str(d), "--vault", str(vault), "--projects", str(projects), "--gate"]

    assert tool.main(args) == 0
    assert "unlabelled       1" in capsys.readouterr().out
    assert tool.main(args + ["--send"]) == 0
    out = capsys.readouterr().out
    assert "both not         1" in out and "1/1 = 100.0%" in out
    assert (vault / "shared" / "feedback" / "merge-it.md").read_text(encoding="utf-8") == before
    assert set(json.loads((d / tool.GATE_LABELS_NAME).read_text(encoding="utf-8"))) == {
        mrc.column(r, mrc.LABEL_SYSTEM) for r in mrc.RATERS}
