"""``tools/measure_guards.py`` over synthetic transcripts, vaults and caches
whose right answers are known by construction (#631).

No model and no network. Pinned: the predicate language parses, refuses what
it cannot evaluate, and matches what it says; turns are numbered as #519
numbers them; the replay fires only forward, in scope, once per turn, and
reads human sessions and dispatched children apart; the proposer never sees
the corrected action and the raters never see the guard; a fire is a true
violation only when both raters say so; the bar names what failed; and the
dry run sends nothing.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_guards as tool  # noqa: E402
from tools import measure_action_reviewer as mar  # noqa: E402

from mnemo.core import llm  # noqa: E402

CWD = "/Users/you/github/app"


def _user(text, ts="2026-09-21T10:00:00Z", cwd=CWD):
    return {"type": "user", "timestamp": ts, "cwd": cwd, "entrypoint": "cli",
            "message": {"role": "user", "content": text}}


def _result(ts="2026-09-21T10:00:00Z"):
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t",
                                                     "content": "ok"}]}}


def _agent(*blocks, ts="2026-09-21T10:00:00Z"):
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": list(blocks)}}


def _text(t):
    return {"type": "text", "text": t}


def _call(name, **args):
    return {"type": "tool_use", "id": "t", "name": name, "input": args}


def _guard(spec):
    return tool.parse_guard(json.dumps(spec))


NPM = {"kind": "action", "tool": "Bash", "field": "command", "pattern": r"\bnpm (i|install)\b",
       "unless": r"--dry-run"}
TESTS_CLAIM = {"kind": "turn", "all": [
    {"final": "(?i)tests? pass"},
    {"no_call_after": {"last": {"tool": "Edit|MultiEdit|Write"},
                       "then": {"tool": "Bash", "field": "command", "pattern": "pytest"}}}]}


# --- the predicate language ------------------------------------------------------------------

def test_parse_guard_reads_each_kind_and_refuses_what_it_cannot_evaluate():
    assert tool.parse_guard('{"kind": "none"}') == {"kind": "none"}
    assert _guard(NPM)["kind"] == "action"
    assert _guard(TESTS_CLAIM)["kind"] == "turn"
    assert tool.parse_guard("no json here")["kind"] == "invalid"
    assert _guard({"kind": "action", "tool": "Bash", "field": "command", "pattern": "("})["kind"] == "invalid"
    # a pattern needs a field to search, a field must be one the language knows
    assert _guard({"kind": "action", "tool": "Bash", "pattern": "x"})["kind"] == "invalid"
    assert _guard({"kind": "action", "tool": "Bash", "field": "stdout", "pattern": "x"})["kind"] == "invalid"
    # a guard that would fire on every call of every tool is no guard
    assert _guard({"kind": "action", "tool": ".*"})["kind"] == "invalid"
    assert _guard({"kind": "turn", "all": []})["kind"] == "invalid"
    assert _guard({"kind": "turn", "all": [{"no_call": {"tool": "Bash"}}]})["kind"] == "invalid"
    assert _guard({"kind": "turn", "all": [{"sometimes": "x"}]})["kind"] == "invalid"
    assert _guard({"kind": "maybe"})["kind"] == "invalid"


def test_an_action_matcher_reads_the_field_it_names():
    g = _guard(NPM)["matcher"]
    assert tool.match_call(g, "Bash", {"command": "cd x && npm install"})
    assert not tool.match_call(g, "Bash", {"command": "npm install --dry-run"})  # unless vetoes
    assert not tool.match_call(g, "Bash", {"command": "yarn install"})
    assert not tool.match_call(g, "Bashful", {"command": "npm install"})  # the tool name matches in full
    content = _guard({"kind": "action", "tool": "Edit|MultiEdit|Write", "field": "content",
                      "pattern": "console\\.log"})["matcher"]
    assert tool.match_call(content, "Write", {"file_path": "a.js", "content": "console.log(1)"})
    assert tool.match_call(content, "MultiEdit", {"file_path": "a.js", "edits": [
        {"old_string": "x", "new_string": "y"}, {"old_string": "z", "new_string": "console.log(2)"}]})
    assert not tool.match_call(content, "Edit", {"file_path": "a.js", "old_string": "console.log(1)",
                                                 "new_string": ""})  # only what goes in


def test_a_path_glob_reads_the_whole_path_or_its_name_on_any_os():
    g = _guard({"kind": "action", "tool": "Edit|Write", "path_glob": "CHANGELOG.md"})["matcher"]
    assert tool.match_call(g, "Edit", {"file_path": "/Users/you/r/CHANGELOG.md"})
    assert tool.match_call(g, "Edit", {"file_path": str(Path("C:/r") / "CHANGELOG.md").replace("/", "\\")})
    assert not tool.match_call(g, "Edit", {"file_path": "/r/changelog.d/1.fixed.md"})
    deep = _guard({"kind": "action", "tool": "Write", "path_glob": "*/migrations/*.sql"})["matcher"]
    assert tool.match_call(deep, "Write", {"file_path": "/r/db/migrations/001.sql"})
    assert not tool.match_call(deep, "Write", {"file_path": "/r/db/001.sql"})


def test_an_action_guard_fires_on_the_first_matching_call_and_a_turn_guard_on_the_turn():
    act = _guard(NPM)
    turn = {"calls": [("Read", {"file_path": "a"}), ("Bash", {"command": "npm i"}),
                      ("Bash", {"command": "npm install"})], "final": ""}
    assert tool.fires(act, turn) == 1
    assert tool.fires(act, {"calls": [], "final": "npm install"}) is None

    claim = _guard(TESTS_CLAIM)
    edited = [("Bash", {"command": "pytest -q"}), ("Edit", {"file_path": "a.py", "new_string": "x"})]
    assert tool.fires(claim, {"calls": edited, "final": "Done, all tests pass."}) == -1
    rerun = edited + [("Bash", {"command": "pytest -q"})]
    assert tool.fires(claim, {"calls": rerun, "final": "Done, all tests pass."}) is None
    assert tool.fires(claim, {"calls": edited, "final": "Done."}) is None
    # no edit at all: "last" never matched, so the condition does not hold
    assert tool.fires(claim, {"calls": [], "final": "tests pass"}) is None
    assert tool.fires({"kind": "none"}, {"calls": edited, "final": "tests pass"}) is None


# --- the turns --------------------------------------------------------------------------------

EVENTS = [_user("install the deps", "2026-09-21T09:50:00Z"),
          _agent(_call("Bash", command="npm install"), _text("Installed with npm."), ts="2026-09-21T09:55:00Z"),
          _result("2026-09-21T09:55:01Z"),
          _user("never use npm here, always yarn", "2026-09-21T10:00:00Z"),
          _agent(_call("Edit", file_path="/r/b.py", old_string="a", new_string="b"), _text("Switched."),
                 ts="2026-09-21T10:01:00Z")]


def test_turns_are_numbered_as_519_numbers_them_and_keep_whole_inputs():
    got = tool.turns(EVENTS)
    assert [t["index"] for t in got] == [w["index"] for w in mar.turn_work(EVENTS)] == [0, 1, 2]
    assert got[1]["prompt"] == "install the deps"
    assert got[1]["calls"] == [("Bash", {"command": "npm install"})]
    assert got[1]["final"] == "Installed with npm."
    assert got[2]["calls"][0][1]["new_string"] == "b"


def test_the_raters_see_the_marked_call_and_never_the_guard():
    t = tool.turns(EVENTS)[1]
    text = tool.render_turn(t, 0)
    assert ">>> MARKED >>> Bash" in text and "npm install" in text and "the marked tool call" in text
    prompt = tool.rate_prompt(tool.rule_text("Yarn", "Never npm."), text)
    for leak in (NPM["pattern"], NPM["unless"], "guard"):
        assert leak not in prompt
    whole = tool.render_turn(t, -1)
    assert ">>> MARKED" not in whole and "the whole turn" in whole


def test_the_proposer_sees_the_page_and_its_quote_and_nothing_later():
    rule = {"title": "Yarn", "body": "Never use npm.", "quote": "always yarn", "universal": False,
            "originals": [("s1", 1)]}
    p = tool.propose_prompt(rule)
    assert "Never use npm." in p and "always yarn" in p and "one project" in p
    assert "s1" not in p and "Installed with npm" not in p


# --- the replay -------------------------------------------------------------------------------

RULES = [
    {"slug": "yarn", "learned": 100.0, "projects": ["app"], "universal": False, "own_sessions": ["src"],
     "originals": [("src", 1)]},
    {"slug": "everywhere", "learned": 100.0, "projects": [], "universal": True, "own_sessions": [],
     "originals": []},
    {"slug": "later", "learned": 500.0, "projects": ["app"], "universal": False, "own_sessions": [],
     "originals": []},
]
NPM_TURN = {"index": 0, "prompt": "p", "calls": [("Bash", {"command": "npm install"}),
                                                 ("Bash", {"command": "npm i x"})], "final": ""}
QUIET = {"index": 1, "prompt": "p", "calls": [("Read", {"file_path": "a"})], "final": ""}


def _sessions():
    meta = lambda project, start, pop: {"project": project, "start": start, "population": pop}  # noqa: E731
    return [
        ("src", meta("app", 50.0, tool.HUMAN), [QUIET, dict(NPM_TURN, index=1)]),   # the original mistake
        ("h1", meta("app", 200.0, tool.HUMAN), [NPM_TURN, QUIET, dict(NPM_TURN, index=2)]),
        ("h2", meta("other", 200.0, tool.HUMAN), [NPM_TURN]),
        ("c1", meta("app", 200.0, tool.CHILD), [NPM_TURN]),
        ("x1", meta("app", 200.0, None), [NPM_TURN]),                              # sdk-cli: not read
        ("old", meta("app", 90.0, tool.HUMAN), [NPM_TURN]),                        # before it was learned
    ]


def test_the_replay_fires_forward_in_scope_once_per_turn_by_population():
    guards = {s: _guard(NPM) for s in ("yarn", "everywhere", "later")}
    fires, originals, exposure = tool.replay(_sessions(), RULES, guards)
    keys = sorted(f["key"] for f in fires)
    assert keys == ["everywhere|c1|0", "everywhere|h1|0", "everywhere|h1|2", "everywhere|h2|0",
                    "yarn|c1|0", "yarn|h1|0", "yarn|h1|2"]
    assert {f["population"] for f in fires if f["session_id"] == "c1"} == {tool.CHILD}
    assert originals["yarn"] == {"src|1": True}
    assert exposure["yarn"] == {tool.HUMAN: 1, tool.CHILD: 1}
    assert exposure["everywhere"] == {tool.HUMAN: 2, tool.CHILD: 1}
    assert exposure["later"] == {tool.HUMAN: 0, tool.CHILD: 0}
    fire = next(f for f in fires if f["key"] == "yarn|h1|0")
    assert fire["at"] == 0 and ">>> MARKED >>> Bash" in fire["text"]


def test_a_rule_without_a_guard_is_exposed_but_never_fires():
    fires, originals, exposure = tool.replay(_sessions(), RULES, {"yarn": {"kind": "none"}})
    assert fires == [] and originals["yarn"] == {"src|1": False} and exposure["yarn"][tool.HUMAN] == 1


def test_the_sample_caps_each_rule_and_population_and_is_seeded():
    fires = [{"key": "a|s%02d|0" % i, "slug": "a", "population": tool.HUMAN} for i in range(30)]
    fires += [{"key": "a|c%02d|0" % i, "slug": "a", "population": tool.CHILD} for i in range(3)]
    got = tool.sample(fires, 5)
    assert sum(f["population"] == tool.HUMAN for f in got) == 5
    assert sum(f["population"] == tool.CHILD for f in got) == 3
    assert [f["key"] for f in got] == [f["key"] for f in tool.sample(list(reversed(fires)), 5)]


def test_population_reads_humans_children_and_neither(tmp_path):
    assert tool.population(EVENTS, "sess0001-x", set()) == tool.HUMAN
    assert tool.population(EVENTS, "sess0001-x", {"sess0001"}) == tool.CHILD
    sdk = [dict(e, entrypoint="sdk-cli") if e["type"] == "user" else e for e in EVENTS]
    assert tool.population(sdk, "sess0002-x", set()) is None


# --- the reading ------------------------------------------------------------------------------

def _fire(slug, sid, turn=0, pop=tool.HUMAN):
    return {"key": "%s|%s|%d" % (slug, sid, turn), "slug": slug, "session_id": sid, "turn": turn,
            "population": pop}


def test_a_true_violation_needs_both_raters_and_unrated_fires_take_their_rules_share():
    fires = [_fire("a", "s1"), _fire("a", "s2"), _fire("a", "s3"), _fire("a", "s4")]
    rated = {"m1": {"a|s1|0": True, "a|s2|0": True}, "m2": {"a|s1|0": True, "a|s2|0": False}}
    assert tool.truth("a|s1|0", rated) is True and tool.truth("a|s2|0", rated) is False
    assert tool.truth("a|s3|0", rated) is None
    w, per = tool.weights(fires, rated)
    assert w["a|s1|0"] == 1.0 and w["a|s2|0"] == 0.0 and w["a|s3|0"] == 0.5
    assert per[("a", tool.HUMAN)] == {"true": 1, "rated": 2}
    rows = tool.session_rows(["s1", "s2", "s3", "s4", "s5"], fires, w)
    assert sum(r["true"] for r in rows) == 2.0 and sum(r["false"] for r in rows) == 2.0
    assert tool.session_rows(["s1"], fires, w, drop="a") == [{"true": 0.0, "false": 0.0}]


def _evaluate(fires, rated, n_sessions=10, kinds=None):
    rules = [{"slug": s, "originals": []} for s in sorted({f["slug"] for f in fires} | {"idle"})]
    guards = {r["slug"]: {"kind": "action", "spec": {}} for r in rules}
    guards.update(kinds or {})
    sessions = {tool.HUMAN: ["s%d" % i for i in range(n_sessions)], tool.CHILD: []}
    return tool.evaluate(rules, guards, fires, {}, {}, sessions, rated, fires)


def _all(fires, m1, m2=None):
    m2 = m1 if m2 is None else m2
    return {"m1": {f["key"]: m1(f) for f in fires}, "m2": {f["key"]: m2(f) for f in fires}}


def test_the_bar_builds_only_when_all_three_hold():
    fires = [_fire("a", "s%d" % i) for i in range(5)] + [_fire("b", "s%d" % i, 1) for i in range(5)]
    data = _evaluate(fires, _all(fires, lambda f: True), n_sessions=10)
    h = data["populations"][tool.HUMAN]
    assert h["estimate"]["E"] == 1.0 and h["precision"]["rate"] == 1.0 and h["false"]["E"] == 0.0
    assert h["carriers"][0][0] == "a" and h["without_top"]["rule"] == "a"
    assert data["verdict"]["decision"] == "build"
    assert data["kinds"]["action"] == 3 and data["kinds"]["none"] == 0


def test_the_bar_names_each_failed_condition():
    # one rule carries every true fire: without it, nothing is left
    fires = [_fire("a", "s%d" % i) for i in range(8)]
    data = _evaluate(fires, _all(fires, lambda f: True), n_sessions=10)
    assert data["verdict"]["decision"] == "do not build"
    assert data["verdict"]["failed"][0].startswith("1. without a")
    # precision 5/10 and 0.5 false fires a session; raters agree, so kappa reads
    fires = [_fire("a", "s%d" % i) for i in range(10)] + [_fire("b", "s%d" % i, 1) for i in range(10)]
    data = _evaluate(fires, _all(fires, lambda f: f["slug"] == "a"), n_sessions=10)
    failed = " ".join(data["verdict"]["failed"])
    assert "2. precision 50.0%" in failed and "3. false fires 1.0000" in failed


def test_no_reading_under_the_kappa_floor_and_pending_while_answers_are_missing():
    fires = [_fire("a", "s%d" % i) for i in range(10)]
    data = _evaluate(fires, _all(fires, lambda f: f["session_id"] in ("s0", "s1", "s2", "s3", "s4"),
                                 lambda f: f["session_id"] in ("s3", "s4", "s5", "s6", "s7")))
    assert data["verdict"]["decision"] == "no reading"
    data = _evaluate(fires, {"m1": {}, "m2": {}})
    assert data["verdict"]["decision"] == "pending"
    data = _evaluate([], {"m1": {}, "m2": {}}, kinds={"idle": {"kind": "none"}})
    assert data["verdict"]["decision"] == "do not build"
    assert data["verdict"]["failed"][0].startswith("1. prevented repeats 0.0000")
    data = _evaluate([], {"m1": {}, "m2": {}}, kinds={"idle": None})
    assert data["kinds"]["pending"] == 1 and data["verdict"]["decision"] == "pending"


def test_report_lines_carry_every_reading():
    fires = [_fire("a", "s%d" % i) for i in range(5)]
    data = _evaluate(fires, _all(fires, lambda f: True))
    data.update({"sources": {"evidence": 1, "known": 0, "linked": 0, "hindsight": 0, "skipped": []},
                 "archived": [], "proposer": "p", "raters": ["m1", "m2"], "sample": 20})
    text = "\n".join(tool.report_lines(data))
    for needle in ("guards: none 0, action 2", "recall on the original mistake", "HUMAN sessions: 10",
                   "CHILD sessions: 0", "precision: 5/5", "carried by: a 5.0", "DECISION"):
        assert needle in text


# --- gathering ----------------------------------------------------------------------------------

def _page(path, slug, body, sources, quote=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    ev = "evidence:\n  quote: '%s'\n" % quote if quote else ""
    path.write_text("---\nname: %s title\nslug: %s\nstability: stable\nsources:\n%s%s---\n%s\n"
                    % (slug, slug, "".join("  - %s\n" % s for s in sources), ev, body), encoding="utf-8")


def test_find_page_reads_a_hand_retired_page_but_never_an_inbox_proposal(tmp_path):
    vault = tmp_path / "vault"
    src = ["bots/app/briefings/sessions/s1.md", "bots/web/briefings/sessions/s2.md"]
    _page(vault / "shared" / "_archive" / "retired-x" / "_inbox" / "feedback" / "gone.proposed.md",
          "gone", "Inbox text.", src[:1])
    assert tool.find_page(vault, "gone") is None
    _page(vault / "shared" / "_archive" / "retired-x" / "feedback" / "gone.md", "gone", "Old body.", src,
          quote="do it yourself")
    got = tool.find_page(vault, "gone")
    assert got["body"] == "Old body." and got["quote"] == "do it yourself" and got["archived"]
    assert got["universal"] and sorted(got["projects"]) == ["app", "web"]
    assert got["sessions"] == ["s1", "s2"] and got["stamped"] is None
    _page(vault / "shared" / "feedback" / "gone.md", "gone", "Live body.", src[:1])
    live = tool.find_page(vault, "gone")
    assert live["body"] == "Live body." and not live["archived"] and live["projects"] == ["app"]


# --- end to end -------------------------------------------------------------------------------

def _transcript(projects, sid, events):
    d = projects / "-Users-you-github-app"
    d.mkdir(parents=True, exist_ok=True)
    (d / (sid + ".jsonl")).write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def test_main_dry_sends_nothing_then_send_asks_each_call_once(tmp_path, capsys, monkeypatch):
    projects, vault = tmp_path / "projects", tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    _transcript(projects, "src00001-x", EVENTS)
    later = [_user("set up the deps", "2026-09-25T09:00:00Z"),
             _agent(_call("Bash", command="npm install left-pad"), _text("Done."), ts="2026-09-25T09:01:00Z"),
             _result("2026-09-25T09:01:01Z"),
             _user("thanks", "2026-09-25T09:02:00Z")]
    _transcript(projects, "new00001-x", later)
    learned = datetime(2026, 9, 22, tzinfo=timezone.utc).timestamp()
    rule = {"slug": "yarn", "title": "Yarn", "body": "Never use npm in this repo.", "quote": "always yarn",
            "learned": learned, "projects": ["app"], "universal": False, "retired": False, "archived": False,
            "own_sessions": ["src00001-x"], "originals": [("src00001-x", 1)]}
    monkeypatch.setattr(tool, "load_rules", lambda v, p, h: ([rule], {
        "evidence": 1, "known": 0, "linked": 0, "hindsight": 0, "skipped": []}))
    base = ["--projects", str(projects), "--vault", str(vault), "--claude-home", str(tmp_path),
            "--out", str(tmp_path / "out"), "--rater", "m1", "--rater", "m2", "--pause", "0", "--json"]
    monkeypatch.setattr(llm, "resolve", lambda cfg: pytest.fail("a dry run must not resolve a provider"))
    assert tool.main(base) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["kinds"]["pending"] == 1 and data["verdict"]["decision"] == "pending"
    assert "proposer claude-opus-5-5: 1 call(s)" in captured.err

    asked = []

    def provider(prompt, *, system, model, timeout):
        asked.append((model, system, prompt))
        body = NPM if system == tool.PROPOSE_SYSTEM else {"broken": True}
        return llm.LLMResponse(text=json.dumps(body), total_cost_usd=0.0, input_tokens=1,
                               output_tokens=1, api_key_source=None, raw={})
    monkeypatch.setattr(llm, "resolve", lambda cfg: provider)
    assert tool.main(base + ["--send"]) == 0
    data = json.loads(capsys.readouterr().out)
    # one run: the proposal, then the replay of its guard, then both raters on the fire
    assert data["kinds"]["action"] == 1
    assert data["per_rule"][0]["originals"] == {"src00001-x|1": True}
    h = data["populations"][tool.HUMAN]
    assert h["sessions"] == 1 and h["fires"] == 1
    assert [s for _, s, _ in asked].count(tool.RATE_SYSTEM) == 2
    rate = next(p for _, s, p in asked if s == tool.RATE_SYSTEM)
    assert "left-pad" in rate and NPM["pattern"] not in rate
    assert h["precision"]["rate"] == 1.0
    assert "provenance" in data

    asked.clear()
    assert tool.main(base + ["--send"]) == 0
    assert asked == []
