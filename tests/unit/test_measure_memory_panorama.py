"""``tools/measure_memory_panorama.py`` over synthetic transcripts, caches and
logs shaped like the real ones (#530). No test calls a model: the tool never does."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_memory_panorama as tool  # noqa: E402

from mnemo.core import llm  # noqa: E402

CWD = "/Users/you/github/app"
START = ("mnemo://v1 project=app\nlocal: [git]\nCall list_rules_by_topic(topic) BEFORE writing code.\n\n"
         "[last-briefing session=s0 date=2026-09-20]\n# Briefing\nWe shipped app__fridays last week.\n"
         "[/last-briefing]\n\n[mnemo learned since your last session]\n• app__no-emoji — No emoji\n"
         "[/mnemo learned]\n\n[mnemo] this repo publishes 2 rule(s) your vault has not imported\n"
         "run mnemo import to see them")
PERSISTED = ("<persisted-output>\nOutput too large (10.4KB). Full output saved to: /Users/you/x.txt\n\n"
             "Preview (first 2KB):\nmnemo://v1 project=app\nlocal: [git]\n\n[last-briefing session=s0]\n"
             "# Briefing\nhalf of it\n...\n</persisted-output>")
REFLEX_0 = ("mnemo reflex context:\n• [[app__small-prs]]: keep PRs small (call read_mnemo_rule if you need the "
            "full file).\n• [[yarn-only]]: use yarn (call read_mnemo_rule if you need the full file).")
ENRICH = "• mnemo rule [[app__lint-first]]:\nRun the linter before committing."
DENY = ("Force-push is blocked.\nRule: shared/feedback/no-force.md\nFix: edit the file to remove or narrow the "
        "enforce block, or run `mnemo disable-rule no-force`.")
LISTING = json.dumps([{"slug": "app__lint-first", "type": "feedback"}, {"slug": "app__other", "type": "reference"}])


def _user(text, ts, *, uuid="u"):
    return {"type": "user", "timestamp": ts, "cwd": CWD, "entrypoint": "cli", "uuid": uuid,
            "message": {"role": "user", "content": text}}


def _agent(text, ts, *tools, model="claude-opus-5-5"):
    return {"type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "model": model, "content": [{"type": "text", "text": text}] + list(tools)}}


def _hook(hook, *texts, kind="hook_additional_context"):
    return {"type": "attachment", "attachment": {"type": kind, "hookName": hook, "content": list(texts)}}


def _tool_use(tid, name, **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def _tool_result(tid, text, ts):
    return {"type": "user", "timestamp": ts, "cwd": CWD, "uuid": "r" + tid,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]}}


def _session():
    return [
        _hook("SessionStart:startup", START, "another plugin's context"),
        _user("install the deps", "2026-09-21T10:00:00Z", uuid="p0"),
        _hook("UserPromptSubmit", REFLEX_0),
        _agent("Listing rules.", "2026-09-21T10:00:05Z",
               _tool_use("t1", "mcp__mnemo__list_rules_by_topic", topic="git"),
               _tool_use("t0", "Bash", command="grep -r disable-rule")),
        _tool_result("t1", LISTING, "2026-09-21T10:00:06Z"),
        # a grep that merely mentions the command is no denial
        _tool_result("t0", "docs: `mnemo disable-rule`, and the rest", "2026-09-21T10:00:06Z"),
        _agent("Reading one.", "2026-09-21T10:00:07Z",
               _tool_use("t2", "mcp__plugin_mnemo_mnemo__read_mnemo_rule", slug="app__lint-first"),
               _tool_use("t3", "Edit", file_path="/Users/you/github/app/x.py")),
        _tool_result("t2", "# lint first\nRun the linter before committing.", "2026-09-21T10:00:08Z"),
        _hook("PreToolUse:Edit", ENRICH),
        _tool_result("t3", "ok", "2026-09-21T10:00:09Z"),
        _user("<bash-input>git status</bash-input>", "2026-09-21T10:00:30Z", uuid="sh"),
        _user("now push it", "2026-09-21T10:01:00Z", uuid="p1"),
        _hook("UserPromptSubmit", kind="hook_cancelled"),
        _agent("Pushing.", "2026-09-21T10:01:05Z", _tool_use("t4", "Bash", command="git push -f")),
        _tool_result("t4", DENY, "2026-09-21T10:01:06Z"),
        _agent("Blocked; asking.", "2026-09-21T10:01:07Z", model="claude-fable-5-1"),
    ]


# --- the envelope --------------------------------------------------------------------------

def test_split_envelope_puts_each_block_in_its_section():
    parts = tool.split_envelope(START)
    assert parts["menu"].startswith("mnemo://v1") and "BEFORE writing code" in parts["menu"]
    assert parts["last_briefing"].startswith("[last-briefing") and parts["last_briefing"].endswith("[/last-briefing]")
    assert "app__no-emoji" in parts["learned"] and "app__no-emoji" not in parts["menu"]
    assert parts["offers"].startswith("[mnemo] this repo publishes") and "mnemo import" in parts["offers"]
    assert parts["predicted"] == ""
    assert "".join(parts.values()).replace("\n", "") == START.replace("\n", "")


def test_a_persisted_envelope_counts_what_the_agent_saw():
    shown, persisted, kb = tool.seen_text(PERSISTED)
    assert persisted and kb == 10.4
    assert shown.startswith("mnemo://v1") and "half of it" in shown and "persisted-output" not in shown
    assert tool.seen_text(START) == (START, False, None)


# --- one transcript --------------------------------------------------------------------------

def test_scan_session_records_every_entry_point_in_order():
    sc = tool.scan_session(_session())
    assert sc["prompts"] == 2  # the ``!`` shell turn is no prompt
    assert len(sc["ss"]) == 1 and not sc["ss"][0]["persisted"]
    assert sc["ss"][0]["sections"]["learned"] > 0 and sc["ss"][0]["sections"]["predicted"] == 0
    assert [(r["i"], r["slugs"]) for r in sc["reflex"]] == [(0, ["app__small-prs", "yarn-only"])]
    assert [c["tool"] for c in sc["mcp"]] == ["list_rules_by_topic", "read_mnemo_rule"]
    assert sc["mcp"][0]["slugs"] == ["app__lint-first", "app__other"]
    assert [e["slugs"] for e in sc["enrich"]] == [["app__lint-first"]]
    assert len(sc["denials"]) == 1 and sc["reflex_cancelled"] == 1
    seqs = [x["seq"] for x in sc["ss"] + sc["reflex"] + sc["mcp"] + sc["enrich"]]
    assert sorted(seqs) == seqs


def test_a_persisted_envelope_is_scanned_by_its_preview():
    sc = tool.scan_session([_hook("SessionStart:startup", PERSISTED), _user("hi", "2026-09-21T10:00:00Z")])
    env = sc["ss"][0]
    assert env["persisted"] and env["reported_kb"] == 10.4
    assert env["bytes"] == len(tool.seen_text(PERSISTED)[0].encode("utf-8"))
    assert tool.envelope_facts({"s": sc}) == {"envelopes": 1, "persisted": 1, "persisted_reported_kb_median": 10.4,
                                              "persisted_seen_bytes_median": env["bytes"],
                                              "learned_seen_in_persisted": 0}


def test_entry_points_average_over_every_session_not_only_those_it_fired_in():
    quiet = tool.scan_session([_hook("SessionStart:startup", "mnemo://v1 project=app\nlocal: [git]"),
                               _user("hi", "2026-09-21T10:00:00Z")])
    got = tool.entry_points({"a": tool.scan_session(_session()), "b": quiet})
    assert got["reflex"]["sessions"] == 1 and got["reflex"]["share_sessions"] == 0.5
    assert got["reflex"]["mean_bytes"] == len(REFLEX_0.encode("utf-8")) / 2
    assert got["session_start: topic menu"]["sessions"] == 2
    assert got["session_start: [mnemo learned]"]["sessions"] == 1
    assert got["pretooluse: enforcement (deny)"]["events"] == 1
    assert got["mcp: get_mnemo_topics"]["events"] == 0
    assert got["reflex"]["mean_tokens"] == got["reflex"]["mean_bytes"] / 4


def test_mcp_chain_follows_a_listing_to_its_read_and_a_reflexed_rule_to_none():
    ch = tool.mcp_chain({"s": tool.scan_session(_session())})
    assert (ch["lists"], ch["list_then_any_read"], ch["list_then_read_of_a_listed_rule"]) == (1, 1, 1)
    assert (ch["reads"], ch["reads_of_a_listed_rule"], ch["reads_of_a_reflexed_rule"]) == (1, 1, 0)
    assert (ch["reflexed_rules"], ch["reflexed_then_read"]) == (2, 0)
    assert (ch["sessions_with_menu"], ch["menu_sessions_that_listed"]) == (1, 1)


def test_access_log_chain_counts_lists_followed_by_a_listed_read():
    rows = [{"timestamp": "2026-09-21T10:00:00Z", "tool": "list_rules_by_topic", "session_id": "a",
             "hit_slugs": ["x", "y"]},
            {"timestamp": "2026-09-21T10:00:05Z", "tool": "read_mnemo_rule", "session_id": "a", "args": {"slug": "y"}},
            {"timestamp": "2026-09-21T10:00:00Z", "tool": "list_rules_by_topic", "session_id": "b", "hit_slugs": ["x"]},
            {"timestamp": "2026-08-01T10:00:00Z", "tool": "list_rules_by_topic", "session_id": "c", "hit_slugs": []},
            {"timestamp": "2026-09-21T10:00:00Z", "tool": "session_start.inject", "session_id": "a"}]
    got = tool.access_log_chain(rows, "2026-09-01")
    assert got == {"sessions": 2, "lists": 2, "list_then_any_read": 1, "list_then_read_of_a_listed_rule": 1,
                   "reads": 1}
    assert tool.access_log_chain(rows, "2026-09-01", {"b"})["lists"] == 1


def test_reflex_log_facts_split_the_judges_scores_by_what_it_injected():
    rows = [{"ts": "2026-09-21T10:00:00Z", "emitted": ["a"], "silence_reason": None,
             "judge": {"status": "ok", "scores": [["a", 0.7], ["b", 0.1]]}},
            {"ts": "2026-09-21T11:00:00Z", "emitted": [], "silence_reason": "judge_none_relevant",
             "judge": {"status": "timeout", "scores": []}},
            {"ts": "2026-08-01T00:00:00Z", "emitted": ["old"], "silence_reason": None}]
    got = tool.reflex_log_facts(rows, "2026-09-01")
    assert (got["rows"], got["emitted"], got["silence"]) == (2, 1, {"judge_none_relevant": 1})
    assert got["judge_status"] == {"ok": 1, "timeout": 1}
    assert got["injected_scores"]["median"] == 0.7 and got["dropped_scores"]["median"] == 0.1


# --- what #520 found of each channel ----------------------------------------------------------

def test_delivered_pairs_attribute_each_rules_own_bytes_and_envelope_section():
    live = {"app__small-prs", "app__yarn-only", "app__lint-first", "app__other", "app__no-emoji", "app__fridays"}
    pairs = tool.delivered_pairs({"s": tool.scan_session(_session())}, {"s": "app"}, live)
    # a pre-rename slug names today's rule
    assert set(pairs["reflex"]) == {("s", "app__small-prs"), ("s", "app__yarn-only")}
    line = next(ln for ln in REFLEX_0.splitlines() if "small-prs" in ln)
    assert pairs["reflex"][("s", "app__small-prs")]["bytes"] == len(line.encode("utf-8"))
    assert set(pairs["mcp listing"]) == {("s", "app__lint-first"), ("s", "app__other")}
    assert set(pairs["mcp read"]) == {("s", "app__lint-first")}
    assert set(pairs["enrichment"]) == {("s", "app__lint-first")}
    assert pairs["session_start"][("s", "app__no-emoji")]["sections"] == ["learned"]
    assert pairs["session_start"][("s", "app__fridays")]["sections"] == ["last_briefing"]


def test_channel_outcomes_and_spend_sort_every_delivery_by_what_520_found():
    live = {"app__small-prs", "app__yarn-only", "app__lint-first", "app__other", "app__no-emoji"}
    pairs = tool.delivered_pairs({"s": tool.scan_session(_session())}, {"s": "app"}, live)
    units = {("s", "app__small-prs"): {"new": True, "redundant": False, "delivered": True},
             ("s", "app__yarn-only"): {"new": False, "redundant": True, "delivered": True},
             ("s", "app__lint-first"): {"new": False, "redundant": False, "delivered": False},
             ("s", "app__gone"): {"new": False, "redundant": False, "delivered": True, "session_start": True}}
    shown = {("s", "app__small-prs"), ("s", "app__yarn-only"), ("s", "app__lint-first"), ("s", "app__no-emoji")}
    got = tool.channel_outcomes(pairs, units, shown, {"s"})
    assert {k: got["reflex"][k] for k in ("delivered", "shown", "relevant", "new", "redundant")} == {
        "delivered": 2, "shown": 2, "relevant": 2, "new": 1, "redundant": 1}
    assert got["enrichment"]["units_520_counted_undelivered"] == 1
    assert got["session_start"]["by_section"]["learned"] == {"delivered": 1, "relevant": 0, "new": 0, "redundant": 0}
    assert got["session_start"]["by_content_only"] == {"units": 1, "new": 0}
    assert tool.channel_outcomes(pairs, units, shown, set())["reflex"]["delivered"] == 0
    spend = tool.spend_by_outcome(pairs, units, shown, {"s"})
    assert spend["reflex"]["new"] > 0 and spend["reflex"]["redundant"] > 0
    assert spend["mcp listing"]["never shown to the raters"] > 0  # app__other
    assert spend["session_start"]["shown, not relevant"] > 0  # app__no-emoji


def test_redundancy_costs_the_bytes_of_units_delivered_but_already_native():
    pairs = {"reflex": {("s", "a"): {"bytes": 100}, ("t", "a"): {"bytes": 50}, ("s", "b"): {"bytes": 7}},
             "mcp read": {("s", "a"): {"bytes": 10}}}
    rows = [{"session_id": "s", "slug": "a", "delivered": True, "redundant": True},
            {"session_id": "t", "slug": "a", "delivered": True, "redundant": True},
            {"session_id": "s", "slug": "b", "delivered": True, "redundant": False},
            {"session_id": "x", "slug": "a", "delivered": True, "redundant": True}]
    got = tool.redundancy(pairs, rows, {"s", "t"}, 1000, {"a": 9})
    assert (got["units"], got["delivered_relevant"], got["bytes"]) == (2, 3, 160)
    assert got["bytes_per_session"] == 80 and got["share_of_all_mnemo_bytes"] == 0.16
    assert got["by_channel"] == {"reflex": 150, "mcp read": 10}
    assert got["top"] == [{"slug": "a", "sessions": 2, "bytes": 160, "reflex_emissions_logged": 9}]


# --- slicing h -----------------------------------------------------------------------------------

def _u(h, model="m", era="before", carriers=("reflex",)):
    return {"h": h, "model": model, "era": era, "carriers": list(carriers)}


def test_era_model_separates_within_a_model_only_with_enough_units_in_each_era():
    few = [_u(1.0, "a")] * 6 + [_u(-1.0, "a", "after")] * 2 + [_u(0.0, "b", "after")] * 6
    got = tool.era_model(few, n_boot=200)
    assert "within_model_difference" not in got and got["raw_difference"] == pytest.approx(1 / 8 * -2 - 1.0)
    enough = [_u(0.5, "a")] * 5 + [_u(0.0, "a", "after")] * 5 + [_u(1.0, "b")] * 5 + [_u(1.0, "b", "after")] * 5
    got = tool.era_model(enough, n_boot=200)
    wm = got["within_model_difference"]
    assert wm["estimate"] == pytest.approx(-0.25) and wm["units"] == 20 and got["models_in_both_eras"] == ["a", "b"]
    assert wm["ci"][0] <= wm["estimate"] <= wm["ci"][1]


def test_era_split_by_carrier_puts_units_the_judge_never_saw_apart():
    units = [_u(1.0, carriers=("session_start",)), _u(-1.0, era="after", carriers=("mcp",)),
             _u(0.0, carriers=("reflex", "mcp"))]
    bc = tool.era_model(units, n_boot=50)["by_carrier"]
    assert bc["not carried by the reflex"]["before"]["h"] == 1.0
    assert bc["not carried by the reflex"]["after"]["h"] == -1.0
    assert bc["carried by the reflex"]["before"]["n"] == 1 and bc["carried by the reflex"]["after"] == {"n": 0}


def test_judge_score_is_the_highest_for_the_rule_up_to_the_prompt():
    rows = [{"session_id": "s", "ts": "2026-09-21T10:00:00Z", "emitted": ["x", "y"],
             "judge": {"scores": [["x", 0.5], ["y", 0.9], ["z", 0.2]]}},
            {"session_id": "s", "ts": "2026-09-21T10:05:00Z", "emitted": ["x"], "judge": {"scores": [["x", 0.7]]}},
            {"session_id": "s", "ts": "2026-09-21T12:00:00Z", "emitted": ["x"], "judge": {"scores": [["x", 0.99]]}},
            {"session_id": "s", "ts": "2026-09-21T10:00:00Z", "emitted": ["q"]}]
    idx = tool.judge_index(rows)
    at = tool.mrc.epoch("2026-09-21T10:10:00Z")
    assert tool.unit_judge_score(idx, "s", "app__x", "app", at) == 0.7
    assert tool.unit_judge_score(idx, "s", "app__z", "app", at) is None  # asked, never injected
    assert tool.unit_judge_score(idx, "t", "app__x", "app", at) is None
    assert [tool.score_bucket(v) for v in (None, 0.4, 0.6, 0.8)] == [
        "no judge score", "judge < 0.6", "judge 0.6-0.8", "judge >= 0.8"]


def test_buckets_and_differences():
    assert [tool.age_bucket(d) for d in (None, 1, 3, 20)] == ["age unknown", "< 3 days", "3-14 days", ">= 14 days"]
    cuts = tool.terciles([1, 2, 3, 4, 5, 6])
    assert cuts == (3, 5) and tool.length_bucket(2, cuts).startswith("short")
    assert tool.length_bucket(5, cuts).startswith("long")
    d = tool.diff_ci([1.0, 1.0, 0.0], [0.0, -1.0], n_boot=200)
    assert d["estimate"] == pytest.approx(2 / 3 + 0.5) and d["n"] == [3, 2]
    assert tool.diff_ci([], [1.0]) is None
    assert tool._signal({"estimate": -0.3, "ci": [-0.5, -0.1]}, -1) == 2
    assert tool._signal({"estimate": -0.3, "ci": [-0.5, 0.1]}, -1) == 1
    assert tool._signal({"estimate": 0.3, "ci": [-0.5, 0.6]}, -1) == 0


# --- the text classes ------------------------------------------------------------------------------

@pytest.mark.parametrize("text,page_type,expected", [
    ("app-release\n2026-09-20: shipped PR #41 (`a1b2c3d`), merged #42, found the bug, fixed it", "project",
     "narrative"),
    ("Ask before pushing\nAlways ask the user for approval before `git push` or `gh pr merge`.", "feedback",
     "preference"),
    ("Release recipe\n1. bump\n2. tag\n3. push", "reference", "procedure"),
    ("Run the suite\nUse `pytest -q` then `mnemo doctor`.", "feedback", "procedure"),
    ("Cron layout\nThe cron lives in `src/cron/a.ts` and reads `jobs.json` via `loadJobs()`.", "reference",
     "project fact or state"),
    ("anything", "project", "project fact or state"),
    ("Small PRs\nKeep pull requests small so review stays quick.", "feedback", "generic practice"),
])
def test_classify_follows_the_printed_rule_set_first_match_wins(text, page_type, expected):
    assert tool.classify(text, page_type) == expected


def test_examples_show_a_rule_once():
    units = [{"slug": "a", "n": i} for i in range(4)] + [{"slug": "b", "n": 9}]
    got = tool.examples(units, tool.random.Random(1))
    assert sorted(u["slug"] for u in got) == ["a", "b"]


# --- privacy ------------------------------------------------------------------------------------------

def test_redact_aliases_private_names_the_home_directory_and_emails(tmp_path):
    tsv = tmp_path / "names.tsv"
    tsv.write_text("acme\trepo-a\nAcme Pay\tpay-x\n\n", encoding="utf-8")
    aliases = tool.load_aliases(tsv)
    assert aliases[0] == ("Acme Pay", "pay-x")
    text = "acme__cron-fix in /Users/you/github/acme, ACME Pay and acmedy; mail ops@acme.com"
    assert tool.redact(text, aliases, "/Users/you") == (
        "repo-a__cron-fix in ~/github/repo-a, pay-x and acmedy; mail <email>")
    assert tool.load_aliases(tmp_path / "missing.tsv") == []


# --- hypotheses -------------------------------------------------------------------------------------

def test_hypotheses_follow_their_evidence_and_rank_by_signal_then_reach():
    data = {
        "units_measured": 10, "days": 30,
        "spend": {"reflex": {"new": 10, "redundant": 10, "relevant, not new": 0, "shown, not relevant": 60,
                             "never shown to the raters": 20}},
        "redundancy": {"units": 6, "delivered_relevant": 10, "share_of_delivered_relevant": 0.6,
                       "bytes_per_session": 400, "tokens_per_session": 100, "share_of_all_mnemo_bytes": 0.04},
        "slices": {"era_model": {"table": {}, "raw_difference": -0.3,
                                 "by_carrier": {"carried by the reflex": {"before": {"n": 3}, "after": {"n": 3}},
                                                "not carried by the reflex": {"before": {"n": 2}, "after": {"n": 2}}}}},
        "unit_groups": {"before reflex": [0.5] * 3, "after reflex": [0.0] * 3,
                        "before other": [1.0] * 2, "after other": [-1.0] * 2},
        "envelope": {"envelopes": 10, "persisted": 2, "persisted_reported_kb_median": 10.4,
                     "persisted_seen_bytes_median": 1960, "learned_seen_in_persisted": 0},
        "agreement": {"kappa": 0.6, "n": 20}, "shares": {"share_tie": 0.4},
        "base": {"rate": 0.5, "h": 0.2, "h_ci": [-0.1, 0.3]},
    }
    hs = tool.hypotheses(data)
    ids = [h["id"] for h in hs]
    era = next(h for h in hs if h["id"] == "H-judge-era")
    # the units the judge never saw dropped too: the claim says it is not the judge's
    assert "not the judge's" in era["claim"] and era["signal"] == 2
    rater = next(h for h in hs if h["id"] == "H-rater-noise")
    assert rater["signal"] == 0 and ids.index("H-rater-noise") > ids.index("H-redundant")
    assert [h["rank"] for h in hs] == list(range(1, len(hs) + 1))
    assert all(h["test"] and h["evidence"] for h in hs)
    keys = [(-h["signal"], -h["reach"]) for h in hs]
    assert keys == sorted(keys)
    few = next(h for h in hs if h["id"] == "H-too-few-chances")
    assert few["signal"] == 0  # 1/15 / 0.5 = 0.133 is under the observed h 0.2
    data["base"] = {"rate": 0.5, "h": 0.05, "h_ci": [-0.1, 0.1]}
    few = next(h for h in tool.hypotheses(data) if h["id"] == "H-too-few-chances")
    assert few["signal"] == 2  # 1/15 / 0.5 = 0.133 is over the CI's upper bound


# --- end to end -------------------------------------------------------------------------------------

def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def _setup(tmp_path):
    vault = tmp_path / "vault"
    for page_type, slug, body in (
            ("feedback", "app__small-prs", "Keep PRs small."),
            ("project", "app__yarn-only", "2026-09-01 shipped PR #12 (`abcdef1`), merged #13, found, fixed, "
                                          "released 2026-09-02, verified 2026-09-03.")):
        _write(vault / "shared" / page_type / (slug + ".md"), "---\nname: %s\ndescription: d\n---\n%s\n" % (slug, body))
    transcript = tmp_path / "projects" / "app" / "s1.jsonl"
    _write(transcript, "\n".join(json.dumps(e) for e in _session()) + "\n")
    start = tool.mrc.epoch("2026-09-21T10:00:00Z")
    state = vault / ".mnemo"
    units = {"since": "2026-09-01", "rated": ["s1"],
             "sessions": {"s1": {"path": str(transcript), "project": "app", "cwd": CWD, "start": start}},
             "columns": {"both": [
                 {"session_id": "s1", "slug": "app__small-prs", "prompts": [0], "new": True, "judged": True,
                  "delivered": True, "redundant": False, "reflex": True, "session_start": False, "mcp": False,
                  "strict": True},
                 {"session_id": "s1", "slug": "app__yarn-only", "prompts": [0], "new": False, "judged": True,
                  "delivered": True, "redundant": True, "reflex": True, "session_start": False, "mcp": False,
                  "strict": False}]}}
    _write(state / "prevented-repeats" / "units.json", units)
    _write(state / "prevented-repeats" / "chunks.json",
           {"s1": [{"id": "c", "rules": ["app__small-prs", "app__yarn-only"], "prompts": []}]})
    uid = tool.bv.unit_id(units["columns"]["both"][0])
    _write(state / "broad-value" / "arms.json", {uid: {
        "id": uid, "slug": "app__small-prs", "project": "app", "model": "claude-opus-5-5", "ts": start + 1,
        "measurable": True, "prompt": "install the deps",
        "carriers": {"session_start": False, "reflex": True, "mcp": False}}})
    col = {r: tool.mrc.column(r, tool.bv.JUDGE_SYSTEM) for r in tool.bv.RATERS}
    _write(state / "broad-value" / "verdicts.json",
           {col[r]: {uid + "|0": {"better": "with", "why": ""}, uid + "|1": {"better": "tie", "why": ""}}
            for r in tool.bv.RATERS})
    _write(state / "broad-value" / "report.json",
           {"results": {"both": {"rate": 1.0, "h": 0.5, "ci": {"h": [0.0, 1.0]}}},
            "shares": {"share_tie": 0.5}, "agreement": {"kappa": 0.3, "n": 2, "same": 2}})
    _write(state / "reflex-log.jsonl", json.dumps(
        {"session_id": "s1", "ts": "2026-09-21T10:00:01Z", "emitted": ["app__small-prs"], "silence_reason": None,
         "judge": {"status": "ok", "scores": [["app__small-prs", 0.85]]}}) + "\n")
    _write(state / "mcp-access-log.jsonl", json.dumps(
        {"timestamp": "2026-09-21T10:00:05Z", "tool": "list_rules_by_topic", "session_id": "s1",
         "hit_slugs": ["app__lint-first"]}) + "\n")
    _write(state / "learned.jsonl", json.dumps({"ts": "2026-09-20T10:00:00", "slug": "app__small-prs"}) + "\n")
    _write(state / "private-names.tsv", "app\trepo-a\n")
    return vault, transcript, start


def test_main_reports_every_part_without_calling_a_model(tmp_path, capsys, monkeypatch):
    vault, transcript, start = _setup(tmp_path)
    monkeypatch.setattr(llm, "resolve", lambda cfg: pytest.fail("the panorama never calls a model"))
    monkeypatch.setattr(tool.mpr, "collect_sessions", lambda projects, v, since: {
        "s1": {"path": str(transcript), "project": "app", "cwd": CWD, "start": start}})
    out = tmp_path / "out"
    args = ["--vault", str(vault), "--projects", str(tmp_path / "projects"), "--out", str(out),
            "--now", "2026-09-28"]
    assert tool.main(args + ["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["since"] == "2026-08-29" and data["sessions_window"] == 1
    assert data["entry_points"]["reflex"]["events"] == 1
    assert data["entry_points"]["pretooluse: enforcement (deny)"]["events"] == 1
    assert data["denial_log"] is None and data["access_log"]["every agent"]["lists"] == 1
    assert data["units_measured"] == 1 and data["base"]["h"] == 0.5
    assert data["slices"]["judge score (reflex units)"] == {
        "judge >= 0.8": {"n": 1, "h": 0.5, "ci": [0.5, 0.5], "wins": 1, "losses": 0}}
    assert data["slices"]["evidence a real correction (#519/#524 raters, both)"]["real correction"]["n"] == 1
    assert data["slices"]["rule age at the prompt"]["< 3 days"]["n"] == 1
    assert data["classes"]["generic practice"]["units"] == 1
    assert data["redundancy"]["units"] == 1 and data["redundancy"]["top"][0]["slug"] == "repo-a__yarn-only"
    assert data["channels"]["reflex"]["relevant"] == 2
    assert data["hypotheses"] and data["hypotheses"][0]["rank"] == 1
    # the private name never leaves: not in the output, not in the saved report
    saved = (out / "report.json").read_text(encoding="utf-8")
    assert "app__" not in saved and "repo-a__small-prs" in saved and "/Users/you" not in saved
    assert json.loads(saved)["units_measured"] == 1

    assert tool.main(args) == 0
    printed = capsys.readouterr().out
    for part in ("== 1. Entry points", "== 2. Why #527", "== 3. The wins", "== 4. The redundancy",
                 "== 5. Hypotheses"):
        assert part in printed
    assert "a lead for a fresh test, not a finding" in printed and "app__" not in printed
