"""``tools/measure_briefing_value.py`` over synthetic transcripts, briefings and raters (#540)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_briefing_value as tool  # noqa: E402

from mnemo.core import llm  # noqa: E402
from mnemo.hooks import session_start as ss  # noqa: E402

R1, R2 = tool.RATERS
CWD = "/Users/you/github/app"
HEAD = "mnemo://v1 project=app\nlocal: [git]"
OLD = "# Briefing\nWe shipped the login page.\n"
START = HEAD + "\n\n[last-briefing session=b-old date=2026-09-19 duration_minutes=5]\n" + OLD + "[/last-briefing]"
LEARNED = "[mnemo learned since your last session]\n• app__no-emoji — No emoji\n[/mnemo learned]"


def _user(text, ts, uuid="u"):
    return {"type": "user", "timestamp": ts, "cwd": CWD, "entrypoint": "cli", "uuid": uuid,
            "message": {"role": "user", "content": text}}


def _agent(text, ts, model="claude-opus-5-5"):
    return {"type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "model": model, "content": [{"type": "text", "text": text}]}}


def _hook(hook, *texts):
    return {"type": "attachment", "attachment": {"type": "hook_additional_context", "hookName": hook,
                                                 "content": list(texts)}}


def _session(start=START, model="claude-opus-5-5"):
    return [
        _hook("SessionStart:startup", start),
        {"type": "attachment", "attachment": {"type": "instructions", "files": [
            {"path": CWD + "/CLAUDE.md", "type": "Project", "content": "Use yarn."}]}},
        _user("<command-name>/model</command-name>", "2026-09-21T10:00:00Z", uuid="c"),
        _user("carry on with the export bug", "2026-09-21T10:00:01Z", uuid="p0"),
        _hook("UserPromptSubmit", "mnemo reflex context:\n• [[app__x]]: x"),
        _agent("Looking at the exporter.", "2026-09-21T10:00:05Z", model=model),
        _user("the csv export still drops the header row", "2026-09-21T10:01:00Z", uuid="p1"),
        _agent("Fixed the header row; PR #12 open.", "2026-09-21T10:02:00Z", model=model),
    ]


def _cand(cid, rank, body, hook_first=False):
    return {"id": cid, "rank": rank, "signals": {}, "hook_first": hook_first, "body": body}


def _unit(uid="s1", hook_first="b-old"):
    return {"id": uid, "project": "app", "first_prompt": "carry on with the export bug",
            "prompts": ["carry on with the export bug", "the csv export still drops the header row"],
            "more_prompts": 0, "short_replies": 1, "final": "Fixed the header row; PR #12 open.",
            "candidates": [_cand("b-old", 0, OLD.strip(), hook_first == "b-old"),
                           _cand("b-export", 1, "# Briefing\nThe csv exporter drops the header row; fix next.",
                                 hook_first == "b-export"),
                           _cand("b-far", 2, "# Briefing\nUnrelated.")]}


def _labels(uid, a_about, a_best, b_about, b_best):
    return {R1: {uid: {"about": a_about, "best": a_best}}, R2: {uid: {"about": b_about, "best": b_best}}}


# --- the unit ----------------------------------------------------------------------------

def test_right_is_the_agreed_best_else_the_newest_both_listed_and_none_without_agreement():
    u = _unit()
    assert tool.choose_right(u, _labels("s1", ["b-export"], "b-export", ["b-export"], "b-export")) == (
        "b-export", "agreed best")
    got = tool.choose_right(u, _labels("s1", ["b-export", "b-far"], "b-far", ["b-export", "b-far"], "b-export"))
    assert got == ("b-export", "newest listed")
    assert tool.choose_right(u, _labels("s1", ["b-export"], "b-export", ["b-far"], "b-far")) is None
    assert tool.choose_right(u, _labels("s1", [], None, [], None)) is None
    assert tool.choose_right(u, {R1: {}, R2: {}}) is None


def test_newest_arm_exists_only_when_the_hooks_pick_was_not_listed():
    assert tool.newest_of(_unit(hook_first="b-old"), {"b-export"}) == "b-old"
    assert tool.newest_of(_unit(hook_first="b-export"), {"b-export"}) is None


def test_first_context_skips_what_the_developer_did_not_type():
    ctx = tool.first_context(_session())
    assert ctx["text"] == "carry on with the export bug" and ctx["model"] == "claude-opus-5-5"
    assert ctx["session_start"] == [START]


def test_strip_briefing_removes_the_block_wherever_it_sits():
    assert tool.strip_briefing(START) == HEAD
    # before #533 [mnemo learned] could follow the briefing: it is kept
    assert tool.strip_briefing(START + "\n\n" + LEARNED) == HEAD + "\n\n" + LEARNED
    assert tool.strip_briefing(HEAD) == HEAD
    assert tool.strip_briefing("[last-briefing session=x]\nbody\n[/last-briefing]") == ""


def test_a_persisted_envelope_is_read_from_its_saved_file_else_the_preview(tmp_path):
    saved = tmp_path / "hook.txt"
    saved.write_text(START + "\n\n" + LEARNED, encoding="utf-8")
    stub = ("<persisted-output>\nOutput too large (9.8KB). Full output saved to: %s\n\nPreview (first 2KB):\n"
            "%s\n...\n</persisted-output>" % (saved, HEAD))
    heads, host, how = tool.envelope_heads([stub])
    assert (heads, host, how) == ([HEAD + "\n\n" + LEARNED], 0, "saved")
    saved.unlink()
    heads, _, how = tool.envelope_heads([stub])
    assert how == "preview" and heads[0].startswith(HEAD) and "persisted-output" not in heads[0]


def test_two_envelopes_stay_two_and_the_briefing_returns_to_its_host():
    heads, host, _ = tool.envelope_heads([HEAD + "\n\n" + LEARNED, START])
    assert heads == [HEAD + "\n\n" + LEARNED, HEAD] and host == 1


def test_envelope_is_the_hooks_assembly_under_533s_cap():
    meta = {"session_id": "b-export", "date": "2026-09-20", "duration_minutes": "7"}
    text, trimmed = tool.envelope(HEAD, "# Briefing\nshort", meta, "b-export", "/v/b-export.md")
    assert text == HEAD + "\n\n[last-briefing session=b-export date=2026-09-20 duration_minutes=7]\n" \
                          "# Briefing\nshort\n[/last-briefing]"
    assert not trimmed
    long_body = "\n".join("line %d %s" % (i, "x" * 80) for i in range(300))
    text, trimmed = tool.envelope(HEAD, long_body, meta, "b-export", "/v/b-export.md")
    assert trimmed and len(text.encode("utf-8")) <= ss.ENVELOPE_MAX_BYTES
    assert ss.BRIEFING_TRIMMED + "/v/b-export.md]" in text and text.endswith("[/last-briefing]")
    assert tool.envelope(HEAD, None, {}, "", "") == (HEAD, False)
    assert tool.envelope("", "body", {}, "b", "/p")[0].startswith("[last-briefing session=b date= ")


def test_arms_differ_only_in_the_briefing_slot():
    u = _unit()
    metas = {"b-export": {"meta": {"session_id": "b-export", "date": "2026-09-20"}, "path": "/v/b-export.md"}}
    arms = tool.build_arms(u, tool.first_context(_session()), "b-export", "agreed best", "b-old", metas)
    p = arms["prompts"]
    assert set(p) == {tool.RIGHT, tool.NONE, tool.NEWEST}
    assert "The csv exporter drops the header row" in p[tool.RIGHT] and "shipped the login" not in p[tool.RIGHT]
    assert "shipped the login page" in p[tool.NEWEST]
    assert "[last-briefing" not in p[tool.NONE] and HEAD in p[tool.NONE]
    for text in p.values():  # what the session had anyway is in every arm
        assert "Use yarn." in text and "[[app__x]]" in text and "carry on with the export bug" in text
    none_lines = set(p[tool.NONE].splitlines())
    assert all(ln in none_lines for ln in p[tool.NEWEST].splitlines()
               if "briefing" not in ln.lower() and "login" not in ln)
    assert arms["work"]["prompts"] == ["the csv export still drops the header row"]
    assert arms["model"] == "claude-opus-5-5" and arms["model_from"] == "transcript"
    no_newest = tool.build_arms(u, tool.first_context(_session()), "b-export", "agreed best", None, metas)
    assert tool.NEWEST not in no_newest["prompts"]


# --- the judge ---------------------------------------------------------------------------

def _arms():
    return tool.build_arms(_unit(), tool.first_context(_session()), "b-export", "agreed best", "b-old", {})


def test_the_rater_never_sees_a_briefing_and_the_order_flips_between_raters():
    arms = _arms()
    answers = {tool.RIGHT: [{"text": "Per [last-briefing session=b-export date=x] b-export: fix the header."}],
               tool.NONE: [{"text": "What export bug?"}]}
    p0 = tool.comparison_prompt(arms, answers, tool.RIGHT, 0, 0)
    p1 = tool.comparison_prompt(arms, answers, tool.RIGHT, 0, 1)
    for p in (p0, p1):
        assert "csv exporter drops" not in p and "b-export" not in p and "[last-briefing" not in p
        assert "the csv export still drops the header row" in p and "PR #12 open" in p
    first = lambda p: p.split("## Reply 1")[1].split("## Reply 2")[0]  # noqa: E731
    assert ("fix the header" in first(p0)) != ("fix the header" in first(p1))
    assert tool.comparison_prompt(arms, answers, tool.RIGHT, 1, 0) is None
    assert tool.comparison_prompt(arms, answers, tool.NEWEST, 0, 0) is None


def test_parse_judge_maps_positions_back_to_arms():
    cid = tool.comparison_id("s1", tool.RIGHT, 0)
    for ri in (0, 1):
        one = tool.treatment_first(cid, ri)
        got = tool.parse_judge(json.dumps({"better": "1", "wrong_state": {"1": True, "2": False}, "why": "w"}),
                               tool.RIGHT, cid, ri)
        assert got["better"] == (tool.RIGHT if one else tool.NONE)
        assert got["wrong"] == ({tool.RIGHT: True, tool.NONE: False} if one else {tool.RIGHT: False, tool.NONE: True})
    tie = tool.parse_judge('{"better": "tie", "wrong_state": {"1": "no", "2": "yes"}}', tool.RIGHT, cid, 0)
    assert tie["better"] == tool.TIE
    assert tool.parse_judge('{"better": "1"}', tool.RIGHT, cid, 0) is None
    assert tool.parse_judge('{"better": "3", "wrong_state": {"1": true, "2": true}}', tool.RIGHT, cid, 0) is None
    assert tool.parse_judge("not json", tool.RIGHT, cid, 0) is None


# --- the numbers -------------------------------------------------------------------------

def _v(better, wa=False, wn=False, arm=tool.RIGHT):
    return {"better": better, "wrong": {arm: wa, tool.NONE: wn}}


def test_both_raters_must_agree_or_it_is_a_tie_and_wrong_needs_both():
    assert tool.agreed([_v("right", True, False), _v("right", True, True)]) == {
        "better": "right", "wrong": {"right": True, "none": False}}
    assert tool.agreed([_v("right"), _v("none")])["better"] == tool.TIE
    assert tool.agreed([_v("right"), None]) is None


def test_unit_scores_and_bootstrap():
    cid = tool.comparison_id
    verdicts = {R1: {cid("a", "right", 0): _v("right", wn=True), cid("a", "right", 1): _v("none"),
                     cid("b", "right", 0): _v("right"), cid("b", "right", 1): _v("right")},
                R2: {cid("a", "right", 0): _v("right", wn=True), cid("a", "right", 1): _v("tie"),
                     cid("b", "right", 0): _v("right"), cid("b", "right", 1): _v("right")}}
    a = tool.unit_scores(verdicts, "a", "right", tool.RATERS)
    assert (a["h"], a["arm"], a["tie"], a["wrong_none"], a["wrong_arm"]) == (0.5, 0.5, 0.5, 0.5, 0.0)
    assert tool.unit_scores(verdicts, "c", "right", tool.RATERS) is None
    st = tool.arm_stats(verdicts, ["a", "b", "c"], "right", tool.RATERS)
    assert st["n"] == 2 and st["h"] == 0.75 and st["h_ci"][0] >= 0.5 and st["h_ci"][1] <= 1.0
    assert st["wrong_diff"] == -0.25
    k = tool.kappa(verdicts, [cid(u, "right", i) for u in "ab" for i in (0, 1)], tool.RATERS)
    assert (k["n"], k["same"]) == (4, 3)


def test_verdicts_follow_the_preset_bar():
    def st(h, lo, hi):
        return {"n": 100, "h": h, "h_ci": [lo, hi]}
    assert tool.verdicts(st(0.3, 0.1, 0.5), st(-0.2, -0.3, -0.1), 0.6) == {
        "ruler": "ok", "right": "a right briefing helps", "wrong": "a wrong briefing hurts"}
    assert tool.verdicts(st(0.02, -0.05, 0.12), st(0.0, -0.1, 0.1), 0.6)["right"] == \
        "a right briefing does not measurably help"
    assert tool.verdicts(st(0.1, -0.05, 0.25), st(0.0, -0.1, 0.1), 0.6) == {
        "ruler": "ok", "right": "inconclusive", "wrong": "not shown to hurt"}
    weak = tool.verdicts(st(0.3, 0.1, 0.5), st(-0.2, -0.3, -0.1), 0.3)
    assert weak["ruler"] == "too weak" and "no verdict" in weak["right"] and "no verdict" in weak["wrong"]
    assert tool.verdicts({"n": 0}, {"n": 0}, None)["right"] == "no estimate yet"


# --- end to end --------------------------------------------------------------------------

def _resp(text):
    return llm.LLMResponse(text=text, total_cost_usd=0.25, input_tokens=10, output_tokens=2,
                           api_key_source=None, raw={})


def _setup(tmp_path):
    vault = tmp_path / "vault"
    d = vault / "bots" / "app" / "briefings" / "sessions"
    d.mkdir(parents=True)
    for sid, body in (("b-old", OLD), ("b-export", "# Briefing\nThe csv exporter drops the header row; fix next.")):
        (d / (sid + ".md")).write_text("---\ntype: briefing\nsession_id: %s\ndate: 2026-09-20\n---\n\n%s\n"
                                       % (sid, body), encoding="utf-8")
    projects = tmp_path / "projects" / "app"
    projects.mkdir(parents=True)
    for sid, model in (("s1", "claude-opus-5-5"), ("s2", "claude-fable-5-1"), ("s3", "claude-opus-5-5")):
        (projects / (sid + ".jsonl")).write_text(
            "\n".join(json.dumps(e) for e in _session(model=model)) + "\n", encoding="utf-8")
    pick = tmp_path / "pick"
    pick.mkdir()
    units = [_unit("s1", "b-old"), _unit("s2", "b-export"), _unit("s3", "b-old")]
    (pick / "units.json").write_text(json.dumps({"units": units}), encoding="utf-8")
    labels = {}
    for r in tool.RATERS:
        col = labels.setdefault(tool.mrc.column(r, tool.bp.SYSTEM), {})
        col["s1"] = col["s2"] = {"about": ["b-export"], "best": "b-export"}
        col["s3"] = {"about": [], "best": None}  # no right briefing: not a unit
    (pick / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
    return ["--vault", str(vault), "--pick", str(pick), "--projects", str(tmp_path / "projects"),
            "--out", str(tmp_path / "out"), "--pause", "0", "--workers", "1"]


def test_run_answers_judges_reports_and_a_rerun_asks_nothing(tmp_path, capsys, monkeypatch):
    base = _setup(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm, "resolve", lambda cfg: pytest.fail("a dry run must not resolve a provider"))
    assert tool.main(base + ["--json"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["counts"]["units"] == 2 and data["counts"]["newest"] == 1 and data["counts"]["newest_right"] == 1
    assert data["pending"]["answers"] == 2 * (3 + 2)
    assert "pending 6 answer call(s)" in captured.err  # s1 on Opus 5.5: three arms, two samples

    asked = []

    def provider(prompt, *, system, model, timeout):
        asked.append((model, system))
        if system == tool.rl.ARM_SYSTEM:
            if "fix next" in prompt:
                return _resp("Fixing the csv header row next.")
            if "login page" in prompt:
                return _resp("The login page is done, so I will polish it.")
            return _resp("Which export bug do you mean?")
        assert system == tool.JUDGE_SYSTEM
        assert "fix next" not in prompt and "shipped the login" not in prompt
        replies = prompt.split("## Reply 1")[1].split("## What the session")[0].split("## Reply 2")
        score = [("header" in r) - ("login" in r) for r in replies]
        better = "1" if score[0] > score[1] else "2" if score[1] > score[0] else "tie"
        return _resp(json.dumps({"better": better, "why": "w",
                                 "wrong_state": {"1": "login" in replies[0], "2": "login" in replies[1]}}))
    monkeypatch.setattr(llm, "resolve", lambda cfg: provider)
    assert tool.main(base + ["--send", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    systems = [s for _, s in asked]
    assert systems.count(tool.rl.ARM_SYSTEM) == 10 and systems.count(tool.JUDGE_SYSTEM) == 2 * 2 * 3
    assert {m for m, s in asked if s == tool.rl.ARM_SYSTEM} == {"claude-opus-5-5", "claude-fable-5-1"}
    both = data["results"]["both"]
    assert both["right"]["n"] == 2 and both["right"]["h"] == 1.0
    assert both["newest"]["n"] == 1 and both["newest"]["h"] == -1.0 and both["newest"]["wrong_arm"] == 1.0
    assert data["kappa"]["kappa"] == 1.0
    assert data["verdicts"]["right"] == "a right briefing helps"
    # Opus 5.5 answered s1 (6 calls) and judged (6): the cost is per model
    assert data["cost"][R1]["calls"] == 12 and data["cost"][R2]["calls"] == 6 + 4

    asked.clear()
    assert tool.main(base + ["--send"]) == 0
    out = capsys.readouterr().out
    assert asked == []
    assert "right briefing: a right briefing helps" in out and "h_right" in out
