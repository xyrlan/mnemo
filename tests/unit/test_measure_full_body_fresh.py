"""``tools/measure_full_body_fresh.py`` over synthetic transcripts, logs and
vaults shaped like the real ones (#545)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_full_body_fresh as tool  # noqa: E402

from mnemo.core import llm  # noqa: E402
from mnemo.core.reflex import render  # noqa: E402

CWD = "/Users/you/github/app"
LIVE = tool.mrc.epoch(tool.LIVE)
DAY = 86400

PIN = ("Pin node 20 in `.nvmrc`.\n\n**Why:** CI runs 20; see [[app__small-prs]].\n- keep the lockfile\n"
       + "\n".join("- detail %d of the node setup" % n for n in range(40)))
SMALL = "Keep PRs small.\n\n**How to apply:** one concern per PR."


def _user(text, ts, *, uuid="u"):
    return {"type": "user", "timestamp": ts, "cwd": CWD, "entrypoint": "cli", "uuid": uuid,
            "message": {"role": "user", "content": text}}


def _agent(text, ts, model="claude-opus-5-5"):
    return {"type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "model": model, "content": [{"type": "text", "text": text}]}}


def _hook(hook, *texts):
    return {"type": "attachment", "attachment": {"type": "hook_additional_context",
                                                 "hookName": hook, "content": list(texts)}}


def _instructions(*files):
    return {"type": "attachment", "attachment": {"type": "instructions", "files": [
        {"path": p, "type": "Project", "content": c} for p, c in files]}}


def _block(max_bytes=render.ENVELOPE_MAX_BYTES):
    return render.render([render.Entry("app__pin-node", "Pin node 20", PIN, "/v/pin.md"),
                          render.Entry("app__small-prs", "Keep PRs small.", SMALL, "/v/small.md")],
                         max_bytes=max_bytes)


def _session(ts, block):
    return [_instructions(("/Users/you/github/app/CLAUDE.md", "Use yarn.")),
            _user("set node up and open the PR", ts, uuid="p0"),
            _hook("UserPromptSubmit", block),
            _agent("Done.", ts)]


# --- the log and the blocks ----------------------------------------------------------------

def test_fresh_rows_are_full_format_emissions_since_go_live_and_the_archive_keeps_them():
    rows = [{"session_id": "a", "format": "full", "emitted": ["x"], "ts": "2026-09-28T23:09:40Z"},
            {"session_id": "b", "format": "full", "emitted": ["x"], "ts": "2026-09-28T22:00:00Z"},
            {"session_id": "c", "format": "preview", "emitted": ["x"], "ts": "2026-09-29T10:00:00Z"},
            {"session_id": "d", "format": "full", "emitted": [], "ts": "2026-09-29T10:00:00Z"},
            {"session_id": "e", "emitted": ["x"], "ts": "2026-09-29T10:00:00Z"}]
    fresh = tool.fresh_rows(rows)
    assert [r["session_id"] for r in fresh] == ["a"]
    # the hook rotated the log: a row it lost stays in the archive, a new one joins it
    later = {"session_id": "f", "format": "full", "emitted": ["y"], "ts": "2026-10-03T10:00:00Z"}
    assert tool.merge_rows(fresh, [later]) == fresh + [later]
    assert tool.merge_rows(fresh + [later], fresh + [later]) == fresh + [later]


def test_entry_whole_tells_a_whole_body_from_a_cut_one_and_a_preview_line():
    whole = _block()
    assert whole.rule_whole == [True, True]
    assert [tool.entry_whole(t) for _, t in tool.mpr.reflex_entries(whole.text)] == [True, True]
    cut = _block(max_bytes=150)
    assert cut.rule_whole[0] is False
    assert tool.entry_whole(tool.mpr.reflex_entries(cut.text)[0][1]) is False
    fallback = render.render([render.Entry("app__gone", "a preview", None),
                              render.Entry("app__small-prs", "Keep PRs small.", SMALL)]).text
    assert [tool.entry_whole(t) for _, t in tool.mpr.reflex_entries(fallback)] == [False, True]


def test_full_emissions_read_full_blocks_only_and_never_a_bodys_links():
    old = "mnemo reflex context:\n• [[app__yarn]]: use yarn, see [[app__npm]] (call read_mnemo_rule …)."
    events = _session("2026-09-29T10:00:00Z", _block().text) + [_hook("UserPromptSubmit", old)]
    got = tool.full_emissions(events)
    assert [(e["slug"], e["whole"]) for e in got] == [("app__pin-node", True), ("app__small-prs", True)]


def test_a_units_whole_or_cut_comes_from_its_log_row_else_from_the_block():
    ts = "2026-09-29T10:00:00Z"
    block = _block(max_bytes=150).text
    ctx = tool.bv.context_at(_session(ts, block), 0)
    row = {"session_id": "s1", "slug": "app__pin-node", "prompts": [0]}
    log = [{"session_id": "s1", "format": "full", "emitted": ["app__pin-node", "app__small-prs"],
            "rule_whole": [False, True], "ts": "2026-09-29T10:00:01Z"}]
    assert tool.unit_reading(row, ctx, "app", log) == {"whole": tool.CUT, "source": "log", "agrees": True}
    # the row rotated away: the entry says the same
    assert tool.unit_reading(row, ctx, "app", []) == {"whole": tool.CUT, "source": "transcript", "agrees": None}
    # a row for another prompt much later is not this block's
    late = [dict(log[0], ts="2026-09-29T11:00:00Z", rule_whole=[True, True])]
    assert tool.unit_reading(row, ctx, "app", late)["source"] == "transcript"
    # a rule the old format carried is no fresh unit
    old = tool.bv.context_at(_session(ts, "mnemo reflex context:\n• [[app__pin-node]]: pin (call …)."), 0)
    assert tool.unit_reading(row, old, "app", log) is None


# --- when it is due ----------------------------------------------------------------------------

def test_due_needs_both_the_days_and_the_units():
    assert tool.due(LIVE + 13 * DAY, 80)["due"] is False
    assert tool.due(LIVE + 15 * DAY, 59)["due"] is False
    assert tool.due(LIVE + 15 * DAY, None) == dict(tool.due(LIVE + 15 * DAY, None), date_ok=True, units_ok=False,
                                                   due=False)
    got = tool.due(LIVE + 14 * DAY, 60)
    assert got["due"] and got["date"] == "2026-10-12T23:09:00Z"


def test_projected_date_is_the_later_of_the_two_conditions():
    # 10 units a day: 60 by day 6, so the 14 days decide
    assert tool.projected(LIVE, 10.0) == "2026-10-12T23:09:00Z"
    # 2 units a day: 60 by day 30
    assert tool.projected(LIVE, 2.0) == "2026-10-28T23:09:00Z"
    assert tool.projected(LIVE, 0.0) is None and tool.projected(LIVE, None) is None


def test_historical_yield_is_reflex_units_over_the_rules_the_reflex_delivered(tmp_path):
    path = tmp_path / "s.jsonl"
    events = _session("2026-09-21T10:00:00Z", "mnemo reflex context:\n• [[a]]: x, see [[b]] (call …).\n"
                                                "• [[c]]: y (call …).")
    source = {"rated": ["s"], "sessions": {"s": {"path": str(path)}},
              "columns": {"both": [{"slug": "a", "new": True, "reflex": True},
                                   {"slug": "c", "new": True, "reflex": True, "judged": False},
                                   {"slug": "d", "new": True, "reflex": False}]}}
    got = tool.historical_yield(source, lambda p: events)
    # the old reading counts the preview's link too: a, b, c
    assert got == {"units": 1, "pairs": 3, "per_pair": 1 / 3}
    assert tool.historical_yield(None, lambda p: []) is None


# --- the numbers -------------------------------------------------------------------------------

def test_h_verdict_uses_the_bar_fixed_in_pr_543():
    assert tool.h_verdict({"n": 60, "h": 0.2, "ci": [0.05, 0.35]}) == "confirmed"
    assert tool.h_verdict({"n": 60, "h": 0.12, "ci": [0.0, 0.25]}) == "inconclusive"
    assert tool.h_verdict({"n": 60, "h": 0.02, "ci": [-0.1, 0.09]}) == "not confirmed"
    assert tool.h_verdict({"n": 60, "h": 0.08, "ci": [0.01, 0.2]}) == "inconclusive"
    assert tool.h_verdict({"n": 0}) == "no estimate yet"


def test_readings_give_both_bars_and_the_whole_cut_split():
    units = [{"id": "u1", "session_id": "s1", "whole": tool.WHOLE},
             {"id": "u2", "session_id": "s1", "whole": tool.CUT},
             {"id": "u3", "session_id": "s2", "whole": tool.WHOLE}]
    arms = {"u1": {"measurable": True}, "u2": {"measurable": True}, "u3": {"measurable": False}}

    def v(b):
        return {"better": b, "why": ""}
    verdicts = {"m1": {"u1|0": v("with"), "u1|1": v("with"), "u2|0": v("without"), "u2|1": v("tie")},
                "m2": {"u1|0": v("with"), "u1|1": v("tie"), "u2|0": v("without"), "u2|1": v("tie")}}
    got = tool.readings(units, ["s1", "s2", "s3"], arms, verdicts, ["m1", "m2"])
    both = got["columns"]["both"]
    assert got["primary"] == "both" and (both["h"]["n"], both["h"]["h"]) == (2, 0.0)
    # three units over three sessions, the unmeasurable one counted in the rate only
    assert (both["product"]["rate"], both["product"]["h"]) == (1.0, 0.0)
    assert got["columns"]["m1"]["h"]["h"] == 0.25
    assert (got["split"]["whole"]["h"], got["split"]["cut"]["h"]) == (0.5, -0.5)


# --- end to end ----------------------------------------------------------------------------------

def _setup(tmp_path):
    projects, vault, out = tmp_path / "projects", tmp_path / "vault", tmp_path / "out"
    proj = projects / "-Users-you-github-app"
    proj.mkdir(parents=True)
    for page_type, slug, body in (("feedback", "app__pin-node", PIN), ("feedback", "app__small-prs", SMALL)):
        path = vault / "shared" / page_type / (slug + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nname: %s\ndescription: d\n---\n%s\n" % (slug, body), encoding="utf-8")
    (vault / "bots").mkdir()
    block = _block(max_bytes=600)
    assert block.rule_whole == [False, True]
    sessions = {"fresh001-x": ("2026-09-30T10:00:00Z", block.text),
                "early002-x": ("2026-09-28T20:00:00Z", block.text)}
    for sid, (ts, text) in sessions.items():
        (proj / (sid + ".jsonl")).write_text("\n".join(json.dumps(e) for e in _session(ts, text)) + "\n",
                                             encoding="utf-8")
    log = [{"session_id": "fresh001-x", "project": "app", "prompt_hash": "sha256:1", "format": "full",
            "emitted": ["app__pin-node", "app__small-prs"], "rule_bytes": block.rule_bytes,
            "rule_whole": block.rule_whole, "ts": "2026-09-30T10:00:01Z"},
           {"session_id": "early002-x", "format": "preview", "emitted": ["app__pin-node"],
            "ts": "2026-09-28T20:00:01Z"}]
    (vault / ".mnemo").mkdir()
    (vault / ".mnemo" / "reflex-log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in log),
                                                       encoding="utf-8")
    # what #520's raters said of the fresh sessions, as its --send leaves it
    pr = out / "prevented-repeats"
    pr.mkdir(parents=True)
    path = str(proj / "fresh001-x.jsonl")

    def row(slug, sid="fresh001-x", **kw):
        r = {"session_id": sid, "slug": slug, "prompts": [0], "new": True, "strict": False,
             "session_start": False, "reflex": True, "mcp": False, "judged": True}
        r.update(kw)
        return r
    (pr / tool.mpr.UNITS_NAME).write_text(json.dumps({
        "since": tool.LIVE, "rated": ["fresh001-x", "early002-x"],
        "sessions": {"fresh001-x": {"path": path, "project": "app", "cwd": CWD, "start": LIVE + 2 * DAY},
                     "early002-x": {"path": path, "project": "app", "cwd": CWD, "start": LIVE - DAY}},
        "columns": {"both": [row("app__pin-node"), row("app__small-prs"),
                             row("app__pin-node", sid="early002-x"),
                             row("app__yarn", reflex=False, session_start=True)]}}), encoding="utf-8")
    return ["--projects", str(projects), "--vault", str(vault), "--out", str(out),
            "--claude-home", str(tmp_path / "home"), "--baseline", str(tmp_path / "none.json"),
            "--rater", "m1", "--rater", "m2", "--pause", "0", "--workers", "1", "--json"]


def _resp(text):
    return llm.LLMResponse(text=text, total_cost_usd=0.25, input_tokens=10, output_tokens=2,
                           api_key_source=None, raw={})


def test_main_counts_fresh_units_and_refuses_to_send_before_it_is_due(tmp_path, capsys, monkeypatch):
    base = _setup(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm, "resolve", lambda cfg: pytest.fail("nothing may be sent before it is due"))
    rated = []
    monkeypatch.setattr(tool.mpr, "main", lambda argv: rated.append(argv) or 0)

    assert tool.main(base, now=LIVE + 2 * DAY) == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["sessions"], data["sessions_with_full"]) == (1, 1)
    assert data["emissions"] == {"n": 2, "whole": 1, "cut": 1}
    assert data["rows"] == {"log": 1, "archive": 1, "human": 1}
    # the early session and the envelope-only unit are not fresh units
    assert (data["rated"], data["units"], data["sources"]) == (1, 2, {"log": 2})
    assert not data["due"]["due"] and data["due"]["date"] == "2026-10-12T23:09:00Z"
    # 2 units in the 1.45 days up to the last rated session: 60 by day 43.5
    assert data["projection"]["date"] == "2026-11-11T12:39:00Z"
    # #527's arms, built without a call: the cut rule's entry goes whole from the without arm
    arms = json.loads((tmp_path / "out" / "broad-value" / "arms.json").read_text(encoding="utf-8"))
    pin = next(a for a in arms.values() if a["slug"] == "app__pin-node")
    assert pin["measurable"] and "Pin node 20" in pin["with"] and "Pin node 20" not in pin["without"]
    assert "cut to fit" in pin["with"] and "cut to fit" not in pin["without"]
    assert "one concern per PR" in pin["without"]

    # --send before the date: refused, #520 not even asked
    assert tool.main(base + ["--send"], now=LIVE + 3 * DAY) == 0
    captured = capsys.readouterr()
    assert "refusing to send" in captured.err and rated == []
    # the date holds but 2 units are under 60: #520 runs, the arms wait
    assert tool.main(base + ["--send"], now=LIVE + 15 * DAY) == 0
    captured = capsys.readouterr()
    assert "refusing to answer and judge: 2 fresh unit(s)" in captured.err
    (argv,) = rated
    assert argv[:4] == ["--since", tool.LIVE, "--until", "2026-10-12T23:09:00Z"] and "--send" in argv


def test_main_with_force_answers_judges_and_prints_both_bars(tmp_path, capsys, monkeypatch):
    base = _setup(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tool.mpr, "main", lambda argv: 0)
    asked = []

    def provider(prompt, *, system, model, timeout):
        asked.append((model, system))
        if system == tool.bv.rl.ARM_SYSTEM:
            return _resp(" ".join(w for w, cue in (("pinned", "Pin node 20"), ("small", "Keep PRs small"))
                                  if cue in prompt) or "anything")
        assert system == tool.bv.JUDGE_SYSTEM
        assert "Pin node 20" not in prompt and "Keep PRs small" not in prompt
        one, two = (sum(w in ("pinned", "small") for w in r.split())
                    for r in prompt.split("## Reply 1")[1].split("## Reply 2"))
        return _resp(json.dumps({"better": "1" if one > two else "2" if two > one else "tie", "why": "fits"}))
    monkeypatch.setattr(llm, "resolve", lambda cfg: provider)
    assert tool.main(base + ["--send", "--force"], now=LIVE + 2 * DAY) == 0
    data = json.loads(capsys.readouterr().out)
    systems = [s for _, s in asked]
    assert systems.count(tool.bv.rl.ARM_SYSTEM) == 8 and systems.count(tool.bv.JUDGE_SYSTEM) == 8
    both = data["results"]["columns"]["both"]
    assert both["h"]["h"] == 1.0 and both["h_verdict"] in ("confirmed", "inconclusive")
    assert (both["product"]["rate"], both["product"]["E"]) == (2.0, 2.0)
    assert data["results"]["split"]["whole"]["n"] == 1 and data["results"]["split"]["cut"]["n"] == 1

    assert tool.main([a for a in base if a != "--json"], now=LIVE + 2 * DAY) == 0
    text = capsys.readouterr().out
    assert "h(full)" in text and "bar: h >= +0.10" in text and "1 per 15" in text
    assert "whole " in text and "cut " in text
