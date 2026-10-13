"""``tools/measure_issue_query.py`` over synthetic transcripts, rankings and scores.

No model and no network. What is pinned is what the measurement has to be
right about: a pointer is a short prompt naming an issue that the agent then
opens with ``gh issue view`` on that number, and its issue text is the call's
result; a child's issue text is the title and body ``_PROMPT`` embeds; the
counters tally what #635's scratch scripts tallied; the child sample is the
first N by ``sha256(session_id)``; the replay is the hook's pre-gate, cap,
dedupe, pool and bar; a session is read only once every injected rule is
labelled; the bootstrap is paired and seeded; and the verdict names every
failed condition, with B-head the target only where B fails.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_issue_query.py"
_spec = importlib.util.spec_from_file_location("measure_issue_query", _TOOL)
miq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(miq)


# --- synthetic transcripts -----------------------------------------------------------

def _user(text, ts="2026-10-11T10:00:00Z", **kw):
    return dict({"type": "user", "timestamp": ts, "cwd": "/w/proj", "message": {"role": "user", "content": text}},
                **kw)


def _use(uid, name, **inp):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": uid, "name": name,
                                                            "input": inp}]}}


def _result(uid, text, error=False):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": uid, "content": text,
                                                     "is_error": error}]}}


def _reflex(*slugs):
    text = "mnemo reflex context:\n" + "\n".join("• [[%s]]: body" % s for s in slugs)
    return {"attachment": {"type": "hook_additional_context", "hookName": "UserPromptSubmit", "content": [text]}}


def _child_prompt(n=42, title="Fix the parser", body="The parser drops\n---\nquoted fields.",
                  head="Work on"):
    return ("%s issue #%d in this repo: %s\n\nRead the full issue with `gh issue view %d` — including its comments,\n"
            "which often re-scope it. The body as it stands:\n\n---\n%s\n---\n\nYou are in a git worktree of your "
            "own on branch `fix/issue-%d`. Work only here.\n" % (head, n, title, n, body, n))


# --- reading ------------------------------------------------------------------------

def test_named_issues_reads_the_scratch_regex():
    assert miq.named_issues("read issue 255 and #129") == {255, 129}
    assert miq.named_issues("leia a issue #635") == {635}
    assert miq.named_issues("fix the 3 bugs") == set()


def test_viewed_issues_skips_flags_and_stops_at_the_segment():
    assert miq.viewed_issues("gh issue view 635 --comments") == [635]
    assert miq.viewed_issues("gh issue view --comments '#12'") == [12]
    assert miq.viewed_issues("cd x && gh issue view https://github.com/o/r/issues/77 | head") == [77]
    assert miq.viewed_issues("gh issue view --json body | jq 5") == []
    assert miq.viewed_issues("gh pr view 12") == []


def test_scan_keeps_the_main_thread_and_the_reflex_rules_before_each_prompt():
    events = [_user("first prompt here"), _reflex("rule-a"), _use("t1", "Read", file_path="x"),
              _result("t1", "contents"),
              dict(_use("t9", "Bash", command="gh issue view 1"), isSidechain=True),
              _user("second prompt", ts="2026-10-11T10:05:00Z"), _reflex("rule-b", "rule-a")]
    got = miq.scan(events)
    assert [t["text"] for t in got["turns"]] == ["first prompt here", "second prompt"]
    assert got["turns"][0]["reflex_before"] == []
    assert got["turns"][1]["reflex_before"] == ["rule-a"]
    assert [u["id"] for u in got["turns"][0]["uses"]] == ["t1"]
    assert got["results"]["t1"]["text"] == "contents"
    assert got["first_reflex"] < got["first_tool"]


def test_a_pointer_needs_the_view_of_a_number_it_names_and_a_result():
    events = [_reflex("earlier"), _user("read issue 255"), _use("t1", "Bash", command="gh issue view 129"),
              _result("t1", "title:\tother"), _use("t2", "Bash", command="gh issue view 255 --comments"),
              _result("t2", "title:\tThe parser\n--\nbody text"),
              _use("t3", "mcp__mnemo__list_rules_by_topic", topic="testing")]
    counts, found = miq.human_pointer(miq.scan(events))
    assert counts == {"prompts": 1, "short_issue_prompts": 1, "read_issue": 1, "listed_after_read": 1}
    assert found["issue"] == 255
    assert found["issue_text"].startswith("title:\tThe parser")
    assert found["prior"] == ["earlier"]


def test_a_long_prompt_an_error_or_no_view_is_no_pointer():
    long = [_user("read issue 255 " + "x" * 300), _use("t1", "Bash", command="gh issue view 255"),
            _result("t1", "body")]
    failed = [_user("read issue 255"), _use("t1", "Bash", command="gh issue view 255"), _result("t1", "nope", True)]
    unread = [_user("read issue 255"), _use("t1", "Read", file_path="x")]
    assert miq.human_pointer(miq.scan(long))[1] is None
    counts, found = miq.human_pointer(miq.scan(failed))
    assert found is None and counts["read_issue"] == 1
    counts, found = miq.human_pointer(miq.scan(unread))
    assert found is None and counts["short_issue_prompts"] == 1 and counts["read_issue"] == 0


def test_a_childs_issue_text_is_the_embedded_title_and_body():
    got = miq.child_opening(miq.scan([_user(_child_prompt())]))
    assert got["issue"] == 42
    assert got["pointer"] == "Work on issue #42 in this repo: Fix the parser"
    assert got["issue_text"] == "Fix the parser\n\nThe parser drops\n---\nquoted fields."
    assert got["prior"] == []
    analysis = miq.child_opening(miq.scan([_user(_child_prompt(head="Investigate"))]))
    assert analysis["pointer"].startswith("Investigate issue #42")
    assert miq.child_opening(miq.scan([_user("Work on issue #42 in this repo: x")])) is None


def test_a_deleted_child_tree_resolves_to_its_main_checkout(tmp_path):
    main = tmp_path / "proj"
    (main / ".git").mkdir(parents=True)
    assert miq.project_of(str(tmp_path / "proj-wt-635")) == "proj"
    assert miq.project_of(str(main)) == "proj"


def test_the_child_sample_is_the_first_n_by_session_hash():
    kids = [{"sid": "s%d" % n} for n in range(30)]
    got = miq.sample_children(kids, n=5)
    want = sorted(kids, key=lambda k: hashlib.sha256(k["sid"].encode()).hexdigest())[:5]
    assert got["taken"] == want and got["eligible"] == 30
    assert got["first_hash"] == hashlib.sha256(want[0]["sid"].encode()).hexdigest()
    assert got["last_hash"] == hashlib.sha256(want[-1]["sid"].encode()).hexdigest()


def test_a_resumed_copy_is_dropped_and_the_earliest_kept():
    kept, dropped = miq.dedupe([{"sid": "b", "start": 2.0, "k": 1}, {"sid": "a", "start": 1.0, "k": 1},
                                {"sid": "c", "start": 3.0, "k": 2}], lambda e: str(e["k"]))
    assert [e["sid"] for e in kept] == ["a", "c"] and dropped == 1


def _write_session(root, project, sid, events):
    d = root / project
    d.mkdir(parents=True, exist_ok=True)
    (d / (sid + ".jsonl")).write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")


def _load(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_collect_counts_like_the_scratch_scripts_and_splits_the_populations(tmp_path):
    _write_session(tmp_path, "p", "h1", [_user("do issue 129"), _use("t1", "Bash", command="gh issue view 129"),
                                        _result("t1", "title:\tX\n--\nbody"), _user("thanks, now continue")])
    _write_session(tmp_path, "p", "h2", [_user("look at #77 please"), _use("t1", "Read", file_path="a")])
    _write_session(tmp_path, "p-wt-42", "c1", [_user(_child_prompt()), _reflex("r"), _use("t1", "Bash", command="ls")])
    _write_session(tmp_path, "p-wt-43", "c2", [_user(_child_prompt(43)), _use("t1", "Bash", command="ls"),
                                              _reflex("r")])
    got = miq.collect(tmp_path, load=_load)
    c = got["counters"]
    assert (c["prompts"], c["short_issue_prompts"], c["read_issue"], c["listed_after_read"]) == (3, 2, 1, 0)
    assert (c["short_issue_sessions"], c["read_sessions"]) == (2, 1)
    assert (c["children"], c["children_reflex_fired"], c["children_fired_before_first_tool"]) == (2, 2, 1)
    assert [e["sid"] for e in got["human"]] == ["h1"]
    assert sorted(e["sid"] for e in got["children"]) == ["c1", "c2"]


class _Rules:
    """``measure_prevented_repeats.Rules``' two calls, over a fixed ranking."""

    def __init__(self, ranking):
        self.ranking = ranking
        self.asked = []

    def pool(self, project, ts, sid):
        return ["r1", "r2", "r3", "r4"]

    def top(self, query, pool, n):
        self.asked.append(query)
        return self.ranking(query)[:n]


def test_build_ranks_every_arm_and_b_head_ranks_only_what_the_judge_reads(tmp_path):
    body = "word " * 400 + "tail"
    _write_session(tmp_path, "p", "c1", [_user(_child_prompt(title="Parser bug", body=body))])
    rules = _Rules(lambda q: ["r1", "r2"] if "tail" in q else ["r3"])
    got = miq.build(tmp_path, rules, cap=10, min_tokens=3, load=_load)
    (e,) = got["sessions"]
    assert e["arms"]["B"]["ranking"] == ["r1", "r2"]
    assert e["arms"]["B-head"]["ranking"] == ["r3"]
    assert e["arms"]["A"]["tokens_ok"] is True
    assert got["children_eligible"] == 1 and got["children_sample"] == 1
    assert "opening" not in e


# --- the replay ---------------------------------------------------------------------

def _entry(sid="s", pop="human", prior=(), a=("r1",), b=("r2", "r3", "r4", "r5"), head=("r2",), ok=True):
    return {"sid": sid, "population": pop, "project": "p", "prior": list(prior),
            "pointer": "do issue 1", "issue_text": "the issue",
            "arms": {"A": {"tokens_ok": ok, "ranking": list(a)}, "B": {"tokens_ok": True, "ranking": list(b)},
                     "B-head": {"tokens_ok": True, "ranking": list(head)}}}


def test_the_judge_sees_the_top_three_minus_what_the_session_was_told():
    e = _entry(prior=["r3"])
    assert miq.candidates(e, "B", cap=10) == ["r2", "r4"]
    assert miq.candidates(e, "A", cap=10) == ["r1"]


def test_the_pre_gate_and_the_cap_silence_an_arm():
    assert miq.candidates(_entry(ok=False), "A", cap=10) == []
    assert miq.candidates(_entry(prior=["x", "y"]), "B", cap=2) == []


def test_needed_asks_once_per_judge_text_and_skips_what_was_asked():
    e = _entry()
    assert miq.needed(e, {}, cap=10) == {"pointer": ["r1"], "issue": ["r2", "r3", "r4"]}
    scores = {}
    miq.merge_score(scores, "s", "issue", ["r2", "r3", "r4"], {"scores": {"r2": 0.9, "r3": 0.1}, "ms": 300})
    assert miq.needed(e, scores, cap=10) == {"pointer": ["r1"]}


def test_injected_uses_the_shipped_bar_and_waits_on_unasked_rules():
    e = _entry()
    scores = {}
    assert miq.injected(e, "B", scores, cap=10) is None
    miq.merge_score(scores, "s", "issue", ["r2", "r3", "r4"], {"scores": {"r2": 0.9, "r3": 0.39}})
    assert miq.injected(e, "B", scores, cap=10) == ["r2"]
    assert miq.injected(e, "B-head", scores, cap=10) == ["r2"]
    assert miq.injected(e, "A", scores, cap=10) is None


def test_a_session_is_read_only_when_every_injected_rule_is_labelled():
    e = _entry()
    scores = {}
    miq.merge_score(scores, "s", "issue", ["r2", "r3", "r4"], {"scores": {"r2": 0.9, "r4": 0.5}})
    miq.merge_score(scores, "s", "pointer", ["r1"], {"scores": {"r1": 0.8}})
    rows, pending = miq.session_rows([e], scores, {"s": {"r1": 0, "r2": 2}}, cap=10)
    assert rows == [] and pending == {"jev": 0, "labels": 1}
    rows, pending = miq.session_rows([e], scores, {"s": {"r1": 0, "r2": 2, "r4": 1}}, cap=10)
    assert rows[0]["arms"]["B"] == {"inj": ["r2", "r4"], "on": ["r2"]}
    assert rows[0]["arms"]["A"] == {"inj": ["r1"], "on": []}


def test_rater_units_carry_the_issue_head_and_the_union_of_injected_rules():
    e = _entry()
    e["issue_text"] = "x" * (miq.RATER_ISSUE_CHARS + 50)
    scores = {}
    miq.merge_score(scores, "s", "issue", ["r2", "r3", "r4"], {"scores": {"r2": 0.9, "r4": 0.5}})
    assert miq.rater_units([e], scores, 10, lambda p, s: "text " + s) == []
    miq.merge_score(scores, "s", "pointer", ["r1"], {"scores": {"r1": 0.8}})
    (u,) = miq.rater_units([e], scores, 10, lambda p, s: "text " + s)
    assert len(u["prompt"]) == miq.RATER_ISSUE_CHARS
    assert [c["slug"] for c in u["pool"]] == ["r1", "r2", "r4"]


# --- readings -----------------------------------------------------------------------

def _row(sid, pop, a_on=(), b_on=(), b_inj=None, head_on=None):
    b_inj = list(b_on) if b_inj is None else list(b_inj)
    head_on = list(b_on) if head_on is None else list(head_on)
    return {"sid": sid, "population": pop,
            "arms": {"A": {"inj": list(a_on), "on": list(a_on)}, "B": {"inj": b_inj, "on": list(b_on)},
                     "B-head": {"inj": list(head_on), "on": list(head_on)}}}


def test_reach_precision_and_load():
    rows = [_row("1", "human", b_on=["x"], b_inj=["x", "y"]), _row("2", "human", a_on=["z"])]
    assert miq.reach(rows, "B") == 0.5 and miq.reach(rows, "A") == 0.5
    assert miq.precision(rows, "B") == 0.5
    assert miq.per_session(rows, "B") == 1.0


def test_the_bootstrap_is_seeded_and_paired():
    rows = [_row(str(n), "human", b_on=["x"]) for n in range(10)]
    assert miq.bootstrap(rows, miq.delta("B"), reps=200) == [1.0, 1.0]
    mixed = rows + [_row("a%d" % n, "human") for n in range(10)]
    assert miq.bootstrap(mixed, miq.delta("B"), reps=300) == miq.bootstrap(mixed, miq.delta("B"), reps=300)


def test_the_top_rule_is_the_one_whose_removal_costs_the_most():
    rows = [_row(str(n), "human", b_on=["big"]) for n in range(5)] + [_row("s", "human", b_on=["small"])]
    assert miq.top_rule(rows, "B") == "big"
    assert miq.carriers(rows, "B")[0] == ("big", 5, 0)


def test_the_verdict_passes_only_when_all_four_hold():
    rows = ([_row("h%d" % n, "human", b_on=["r%d" % n]) for n in range(10)]
            + [_row("c%d" % n, "children", b_on=["q%d" % n]) for n in range(30)]
            + [_row("z%d" % n, "children") for n in range(20)])
    human = [r for r in rows if r["population"] == "human"]
    v = miq.verdict(rows, human, "B")
    assert v["pass"], v["failed"]


def test_the_verdict_names_each_failed_condition():
    one_rule = [_row("c%d" % n, "children", b_on=["only"], b_inj=["only"] + ["n%d" % k for k in range(5)])
                for n in range(20)] + [_row("h", "human", a_on=["a"], b_on=[])]
    v = miq.verdict(one_rule, [r for r in one_rule if r["population"] == "human"], "B")
    assert [f.split(" ", 1)[0] for f in v["failed"]] == ["2", "3", "4"]
    flat = [_row(str(n), "human") for n in range(5)]
    v = miq.verdict(flat, flat, "B")
    assert [f.split(" ", 1)[0] for f in v["failed"]] == ["1", "2", "3", "4"]


def test_b_head_is_the_target_only_where_b_fails():
    ok = {"pass": True, "failed": []}
    bad = {"pass": False, "failed": ["2 precision < 23%"]}
    assert miq.decision(ok, bad).startswith("BUILD (B:")
    assert miq.decision(bad, ok).startswith("BUILD (B-head")
    assert miq.decision(bad, dict(bad, failed=["1 reach", "4 without"])) == \
        "DO NOT BUILD (B failed 2; B-head failed 1, 4)"


def test_readings_and_the_report_render_every_population():
    rows = ([_row("h%d" % n, "human", b_on=["r"]) for n in range(3)]
            + [_row("c%d" % n, "children", a_on=["a"]) for n in range(3)])
    r = miq.readings(rows)
    assert r["arms"]["human"]["B"]["reached"] == 3
    assert r["arms"]["children"]["A"]["reached"] == 3
    data = {"counters": dict.fromkeys(["prompts", "short_issue_prompts", "read_issue", "listed_after_read",
                                       "short_issue_sessions", "read_sessions", "children",
                                       "children_reflex_fired", "children_fired_before_first_tool"], 0),
            "human_eligible": 3, "human_duplicates": 0, "children_eligible": 3, "children_duplicates": 0,
            "children_sample": 3, "sample_first_hash": "ab", "sample_last_hash": "cd", "pool": 3, "at": 0.4,
            "cap": 10, "min_tokens": 3, "complete": 6, "sessions": 6, "pending": {"jev": 0, "labels": 0},
            "rater": "m@x", "readings": r}
    text = "\n".join(miq.report_lines(data))
    assert "VERDICT: DO NOT BUILD" in text
    assert "children (3 sessions)" in text
