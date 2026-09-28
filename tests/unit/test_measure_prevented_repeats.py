"""``tools/measure_prevented_repeats.py`` over synthetic transcripts and vaults
shaped like the real ones (#520)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_prevented_repeats as tool  # noqa: E402

from mnemo.core import llm  # noqa: E402

CWD = "/Users/you/github/app"


def _user(text, ts, *, uuid="u"):
    return {"type": "user", "timestamp": ts, "cwd": CWD, "entrypoint": "cli", "uuid": uuid,
            "message": {"role": "user", "content": text}}


def _agent(text, ts, *tools):
    content = [{"type": "text", "text": text}] + list(tools)
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": content}}


def _hook(hook, *texts):
    return {"type": "attachment", "attachment": {"type": "hook_additional_context",
                                                 "hookName": hook, "content": list(texts)}}


def _instructions(*files):
    return {"type": "attachment", "attachment": {"type": "instructions", "files": [
        {"path": p, "type": "Project", "content": c} for p, c in files]}}


def _tool_use(tid, name, **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def _tool_result(tid, text, ts):
    return {"type": "user", "timestamp": ts, "cwd": CWD, "uuid": "r" + tid,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": text}]}}


START = ("mnemo://v1 project=app\nlocal: [git]\n\n[last-briefing session=s0 date=2026-09-20]\n"
         "# Briefing\nWe agreed yarn only.\n[/last-briefing]\n\n[mnemo learned since your last session]\n"
         "• app__no-emoji — No emoji in commits\n[/mnemo learned]")


def _session():
    return [
        _hook("SessionStart:startup", START, "some other plugin's context"),
        _instructions(("/Users/you/github/app/CLAUDE.md", "Use yarn.")),
        _user("install the deps and run the tests", "2026-09-21T10:00:00Z", uuid="p0"),
        _hook("UserPromptSubmit", "mnemo reflex context:\n• [[yarn-only]]: use yarn (call read_mnemo_rule …)."),
        _agent("Running npm install.", "2026-09-21T10:00:05Z",
               _tool_use("t1", "mcp__plugin_mnemo_mnemo__read_mnemo_rule", slug="app__pin-node")),
        _tool_result("t1", "# pin node\nPin node 20.", "2026-09-21T10:00:06Z"),
        _agent("Done.", "2026-09-21T10:00:07Z"),
        _user("<command-name>/clear</command-name>", "2026-09-21T10:00:30Z", uuid="x"),
        _user("now commit it", "2026-09-21T10:01:00Z", uuid="p1"),
        _agent("Committed.", "2026-09-21T10:01:05Z"),
    ]


# --- what each prompt had in context ----------------------------------------------------

def test_walk_snapshots_each_prompt_after_its_own_hook_context():
    w = tool.walk(_session())
    assert [p["text"] for p in w["prompts"]] == ["install the deps and run the tests", "now commit it"]
    first, second = w["prompts"]
    assert first["i"] == 0 and second["i"] == 1
    # the reflex block that follows prompt 0 is prompt 0's; the MCP read comes after it
    assert first["reflex"] == ["yarn-only"] and first["mcp"] == []
    assert second["reflex"] == ["yarn-only"] and second["mcp"] == ["app__pin-node"]
    assert second["answered"] == "Running npm install.\nDone." or "Done." in second["answered"]
    # only mnemo's SessionStart text counts, and the instruction files are recorded
    assert w["session_start"] == [START] and first["session_start_n"] == 1
    assert first["native"] == [["/Users/you/github/app/CLAUDE.md", "Use yarn."]]
    assert w["mnemo_chars"] == len(START) + len(_session()[3]["attachment"]["content"][0]) + len(
        "# pin node\nPin node 20.")


def test_canonical_maps_a_pre_rename_slug_to_its_project_prefix():
    live = {"app__yarn-only", "universal-rule"}
    assert tool.canonical("yarn-only", "app", live) == "app__yarn-only"
    assert tool.canonical("universal-rule", "app", live) == "universal-rule"
    assert tool.canonical("gone", "app", live) == "gone"


def test_unit_outcome_splits_channels_and_redundancy():
    w = tool.walk(_session())
    live = {"app__yarn-only", "app__pin-node", "app__no-emoji", "app__other"}
    p0, p1 = w["prompts"]
    reflex = tool.unit_outcome("app__yarn-only", [p0], w["session_start"], {"A": False, "B": False}, "app", live)
    assert reflex["reflex"] and not reflex["mcp"] and reflex["new"]
    # the MCP read happened after prompt 0, so it counts only for prompt 1
    assert not tool.unit_outcome("app__pin-node", [p0], w["session_start"], None, "app", live)["mcp"]
    assert tool.unit_outcome("app__pin-node", [p1], w["session_start"], None, "app", live)["mcp"]
    # the learned block names the slug; redundant when Claude Code's own files say it
    named = tool.unit_outcome("app__no-emoji", [p0], w["session_start"], {"A": False, "B": True}, "app", live)
    assert named["session_start"] and named["redundant"] and not named["new"]
    # the judge reading the briefing
    judged = tool.unit_outcome("app__other", [p0], w["session_start"], {"A": True, "B": False}, "app", live)
    assert judged["session_start"] and judged["new"]
    assert not tool.unit_outcome("app__other", [p0], w["session_start"], None, "app", live)["delivered"]


# --- the strict pool --------------------------------------------------------------------

def _rule(vault, slug, body, *, sources, extra=""):
    path = vault / "shared" / "feedback" / (slug + ".md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\nname: %s\ndescription: d\nstability: stable\nsources:\n%s%s---\n%s\n"
                    % (slug, "".join("  - %s\n" % s for s in sources), extra, body), encoding="utf-8")


def _src(sid):
    return "bots/app/briefings/sessions/%s.md" % sid


def _evidence(quote, sid):
    return "confidence: verified\nevidence:\n  quote: '%s'\n  source: '%s'\n" % (quote, _src(sid))


def test_evidence_items_are_placed_at_their_turn_or_kept_as_the_quote(tmp_vault, tmp_path):
    from mnemo.core.reflex import replay

    _rule(tmp_vault, "yarn-only", "Always yarn.", sources=[_src("old1")],
          extra=_evidence("never use npm here", "old1"))
    _rule(tmp_vault, "gone-src", "Something.", sources=[_src("lost")],
          extra=_evidence("stop doing that thing", "lost"))
    _rule(tmp_vault, "no-quote", "Inferred.", sources=[_src("old1")])
    t = tmp_path / "old1.jsonl"
    t.write_text("\n".join(json.dumps(e) for e in [
        _agent("I ran npm install.", "2026-09-01T10:00:00Z"),
        _user("never use npm here, it is a yarn repo", "2026-09-01T10:01:00Z")]) + "\n", encoding="utf-8")
    got = tool.evidence_items(tmp_vault, replay.rule_facts(tmp_vault), {"old1": t})
    assert set(got) == {"yarn-only", "gone-src"}
    assert got["yarn-only"]["on_disk"] and got["yarn-only"]["answered"] == "I ran npm install."
    assert got["yarn-only"]["id"] == tool.mrc.item_id("old1", 0, "never use npm here")
    assert not got["gone-src"]["on_disk"] and got["gone-src"]["turn"] == "stop doing that thing"


def test_strict_pool_waits_for_every_label_and_needs_both_raters():
    ev = {"a": {"id": "1"}, "b": {"id": "2"}}
    assert tool.strict_pool(ev, {"m1": {"1": True}, "m2": {"1": True}}) is None
    assert tool.strict_pool(ev, {"m1": {"1": True, "2": True}, "m2": {"1": True, "2": False}}) == {"a"}


# --- rating -----------------------------------------------------------------------------

def _chunk():
    return {"id": "c1", "session_id": "s", "rules": ["yarn-only", "no-emoji"],
            "prompts": [{"key": "s#0", "text": "install", "answered": ""},
                        {"key": "s#1", "text": "commit", "answered": "ok"}]}


def test_freq_prompt_numbers_rules_and_prompts_without_naming_slugs():
    text = tool.freq_prompt(_chunk(), lambda s: "rule text for %s" % s.upper())
    assert "### R2\nrule text for NO-EMOJI" in text and "### P2" in text
    assert "yarn-only" not in text


def test_parse_freq_needs_every_prompt_and_drops_unknown_rules():
    ch = _chunk()
    ok = json.dumps({"prompts": [{"id": "P1", "rules": ["R1", "R9"]}, {"id": "P2", "rules": []}]})
    assert tool.parse_freq(ok, ch) == {"s#0": ["yarn-only"], "s#1": []}
    assert tool.parse_freq(json.dumps({"prompts": [{"id": "P1", "rules": []}]}), ch) is None
    assert tool.parse_freq("not json", ch) is None


def test_relevant_both_is_the_intersection_over_prompts_every_rater_answered():
    ch = _chunk()
    freq = {"m1": {"c1": {"s#0": ["yarn-only", "no-emoji"], "s#1": []}},
            "m2": {"c1": {"s#0": ["yarn-only"], "s#1": ["no-emoji"]}}}
    rel = tool.relevant(freq, ["m1", "m2"], [ch])
    assert rel["both"] == {"s#0": {"yarn-only"}, "s#1": set()}
    assert tool.relevant({"m1": freq["m1"], "m2": {}}, ["m1", "m2"], [ch])["both"] == {}


def test_parse_delivery_maps_rows_to_units_and_needs_both_booleans():
    got = tool.parse_delivery(json.dumps({"rules": [{"id": "R1", "A": True, "B": False},
                                                    {"id": "R2", "A": True},
                                                    {"id": "R9", "A": False, "B": False}]}), ["u1", "u2"])
    assert got == {"u1": {"A": True, "B": False}}
    assert tool.parse_delivery("nope", ["u1"]) == {}


def test_delivery_batches_group_units_by_their_shared_texts():
    def unit(uid, texts, empty=False):
        return {"id": uid, "texts": texts, "empty": empty, "rule": "rule " + uid, "ss": "A " + texts,
                "files": [("CLAUDE.md", "B " + texts)], "notes": [("n", uid)]}
    units = {("s", str(i)): unit("u%d" % i, "t1") for i in range(3)}
    units[("s2", "x")] = unit("v", "t2")
    units[("s3", "y")] = unit("w", "t3", empty=True)
    batches = tool.delivery_batches(units, {"u0": {}}, size=1)
    assert [[u["id"] for u in b] for b in batches] == [["u1"], ["u2"], ["v"]]
    text = tool.batch_prompt([units[("s", "1")], units[("s", "2")]])
    assert text.count("## Text A") == 1 and "### R2\nrule u2" in text and "memory note n\nu1" in text


# --- the numbers ------------------------------------------------------------------------

def _rows(n_sessions, new_in_first):
    rows = [{"units": 0, "new": 0, "delivered": 0, "redundant": 0, "prompts": 10,
             "session_start": 0, "reflex": 0, "mcp": 0} for _ in range(n_sessions)]
    for i in range(new_in_first):
        rows[i].update(units=2, new=1, delivered=1, reflex=1)
    return rows


def test_combine_is_frequency_times_delivery_times_lift():
    stats = tool.combine(_rows(20, 10), [0.3] * 10, n_boot=200)
    assert stats["F"] == 1.0 and stats["D"] == 0.5 and stats["L"] == pytest.approx(0.3)
    assert stats["E"] == pytest.approx(0.15) and stats["per_prompt"] == pytest.approx(0.1)
    assert stats["channels"]["reflex"] == 0.5
    lo, hi = stats["ci"]["E"]
    assert lo <= 0.15 <= hi and not stats["widened"]


def test_combine_widens_the_upper_bound_when_nothing_was_delivered():
    stats = tool.combine(_rows(50, 0), [0.3] * 10, n_boot=100)
    assert stats["E"] == 0 and stats["D"] is None and stats["widened"]
    assert stats["ci"]["E"][1] == pytest.approx(tool.poisson_upper(0) / 50 * 0.3)


def test_poisson_upper_matches_the_exact_table():
    assert tool.poisson_upper(0) == pytest.approx(3.689, abs=1e-3)
    assert tool.poisson_upper(5) == pytest.approx(11.668, abs=1e-3)


def test_verdict_uses_the_preset_thresholds():
    bar = tool.THRESHOLD
    assert tool.verdict(bar, 0.001, 0.2) == "positive"
    assert tool.verdict(bar, 0.0, 0.2) == "inconclusive"  # lower bound must be > 0
    assert tool.verdict(0.01, 0.0, bar - 1e-9) == "null"
    assert tool.verdict(0.05, 0.01, 0.1) == "inconclusive"
    assert tool.verdict(None, None, None) == "no estimate yet"


def test_needed_sessions_scale_the_half_width():
    stats = {"sessions": 40, "E": 0.02, "ci": {"E": [0.0, 0.1]}}
    # half-width up 0.08 against a gap of 0.0467: (0.08/0.0467)^2 x 40
    assert tool.needed_sessions(40, stats) == 118
    assert tool.needed_sessions(40, {"sessions": 40, "E": 0.02, "ci": {"E": [0.0, 0.03]}}) == 40


def test_bottleneck_is_frequency_when_perfect_delivery_stays_under_the_bar():
    low_f = {"E": 0.001, "F": 0.1, "D": 0.1, "L": 0.3, "channels": {"redundant": 0.0}}
    assert tool.bottleneck(low_f).startswith("frequency")
    low_d = {"E": 0.01, "F": 1.0, "D": 0.03, "L": 0.3, "channels": {"redundant": 0.0}}
    assert tool.bottleneck(low_d).startswith("delivery")
    # the same numbers, but most relevant rules were already in CLAUDE.md / auto-memory
    mostly_native = dict(low_d, F=0.4, channels={"redundant": 0.625})
    assert tool.bottleneck(mostly_native).startswith("frequency")
    assert tool.bottleneck({"E": 0.5, "F": 1, "D": 1, "L": 0.5}).startswith("none")


def test_session_order_is_seeded_and_a_prefix_is_stable():
    ids = ["s%02d" % i for i in range(30)]
    assert tool.session_order(ids) == tool.session_order(list(reversed(ids)))
    assert tool.session_order(ids) != sorted(ids)


def test_per_session_counts_keep_empty_sessions_and_filter_strict():
    units = {"a": [dict(strict=True, new=True, delivered=True, redundant=False, session_start=False,
                        reflex=True, mcp=False),
                   dict(strict=False, new=False, delivered=False, redundant=True, session_start=False,
                        reflex=False, mcp=False)]}
    rows = tool.per_session_counts(units, {"a": 3, "b": 5}, tool.STRICT)
    assert [(r["units"], r["new"], r["prompts"]) for r in rows] == [(1, 1, 3), (0, 0, 5)]
    assert tool.per_session_counts(units, {"a": 3}, tool.BROAD)[0]["units"] == 2


# --- sending ----------------------------------------------------------------------------

def _resp(text):
    return llm.LLMResponse(text=text, total_cost_usd=0.5, input_tokens=10, output_tokens=2,
                           api_key_source=None, raw={})


def test_sender_logs_every_answer_and_stops_after_failures_in_a_row(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "MAX_FAILURES", 2)
    got = []

    def provider(prompt, *, system, model, timeout):
        if prompt.startswith("boom"):
            raise llm.LLMSubprocessError("x")
        return _resp(prompt.upper())
    s = tool.Sender(provider, 5, tmp_path / "calls.jsonl", workers=1, pause=0)
    s.run([("m1", "one", "sys", got.append), ("m1", "boom1", "sys", got.append),
           ("m1", "two", "sys", got.append)])
    assert got == ["ONE", "TWO"] and s.usd == 1.0 and not s.stopped
    s.run([("m1", "boom2", "sys", got.append), ("m1", "boom3", "sys", got.append),
           ("m1", "three", "sys", got.append)])
    assert s.stopped and got == ["ONE", "TWO"]
    assert tool.mrc.spent_by_rater(tmp_path / "calls.jsonl", ["m1"]) == {"m1": {"usd": 1.0, "calls": 2}}


# --- end to end -------------------------------------------------------------------------

def _setup(tmp_path):
    projects, vault = tmp_path / "projects", tmp_path / "vault"
    proj = projects / "-Users-you-github-app"
    proj.mkdir(parents=True)
    (vault / "shared").mkdir(parents=True)
    (vault / "bots").mkdir()
    teach = [_agent("Running npm install.", "2026-09-01T10:00:00Z"),
             _user("never use npm in this repo, always yarn", "2026-09-01T10:01:00Z", uuid="t0")]
    later = [_hook("SessionStart:startup", START),
             _user("install the deps please and run the test suite", "2026-09-21T10:00:00Z", uuid="p0"),
             _hook("UserPromptSubmit", "mnemo reflex context:\n• [[app__yarn-only]]: yarn."),
             _agent("On it.", "2026-09-21T10:00:05Z"),
             _user("thanks, looks good", "2026-09-21T10:02:00Z", uuid="p1"),
             _agent("Great.", "2026-09-21T10:02:05Z")]
    for sid, events in (("teach001-x", teach), ("later002-x", later)):
        (proj / (sid + ".jsonl")).write_text("\n".join(json.dumps(e) for e in events) + "\n",
                                             encoding="utf-8")
    _rule(vault, "app__yarn-only", "Never use npm in this repo; always yarn.",
          sources=[_src("teach001-x")], extra=_evidence("never use npm in this repo", "teach001-x"))
    return projects, vault


def test_main_labels_rates_judges_then_reports_and_a_rerun_asks_nothing(tmp_path, capsys, monkeypatch):
    projects, vault = _setup(tmp_path)
    home = tmp_path / "claude-home"
    home.mkdir()
    monkeypatch.chdir(tmp_path)
    base = ["--projects", str(projects), "--vault", str(vault), "--claude-home", str(home),
            "--out", str(tmp_path / "out"), "--rater", "m1", "--rater", "m2", "--pause", "0",
            "--since", "2026-09-10"]
    monkeypatch.setattr(llm, "resolve", lambda cfg: pytest.fail("a dry run must not resolve a provider"))
    assert tool.main(base + ["--json"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["population"] == 1 and data["strict_size"] is None
    assert "pending 1 label" in captured.err

    asked = []

    def provider(prompt, *, system, model, timeout):
        asked.append((model, system))
        if system == tool.mrc.LABEL_SYSTEM:
            ids = [line.split()[-1] for line in prompt.splitlines() if line.startswith("### item")]
            return _resp(json.dumps({"labels": [{"id": i, "correction": True} for i in ids]}))
        if system == tool.FREQ_SYSTEM:
            assert "Never use npm" in prompt and "teach001" not in prompt
            return _resp(json.dumps({"prompts": [{"id": "P1", "rules": ["R1"]}, {"id": "P2", "rules": []}]}))
        assert system == tool.DELIVERY_SYSTEM and "[last-briefing" in prompt
        return _resp(json.dumps({"rules": [{"id": "R1", "A": False, "B": False}]}))
    monkeypatch.setattr(llm, "resolve", lambda cfg: provider)
    assert tool.main(base + ["--send", "--json", "--workers", "1"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["strict_size"] == 1 and data["rated"] == 1
    both = data["results"]["strict"]["both"]
    assert (both["units"], both["new"], both["F"], both["D"]) == (1, 1, 1.0, 1.0)
    assert both["channels"]["reflex"] == 1.0 and both["channels"]["session_start"] == 0.0
    assert [s for _, s in asked].count(tool.FREQ_SYSTEM) == 2
    assert [s for _, s in asked].count(tool.DELIVERY_SYSTEM) == 2
    assert data["cost"]["mnemo"]["sessions"] == 1

    asked.clear()
    assert tool.main(base + ["--send", "--json"]) == 0
    assert asked == [] and json.loads(capsys.readouterr().out)["rated"] == 1


def test_walk_skips_shell_turns_but_keeps_the_turn_index():
    events = [_user("<bash-input>git status</bash-input>", "2026-09-21T10:00:00Z", uuid="b"),
              _user("<bash-stdout>clean</bash-stdout><bash-stderr></bash-stderr>", "2026-09-21T10:00:01Z", uuid="c"),
              _user("please open the PR", "2026-09-21T10:01:00Z", uuid="p")]
    w = tool.walk(events)
    assert [(p["i"], p["text"]) for p in w["prompts"]] == [(2, "please open the PR")]


def test_regime_split_cuts_at_the_judge_going_live():
    cut = tool.mrc.epoch(tool.JUDGE_LIVE)
    sessions = {"a": {"start": cut - 1}, "b": {"start": cut}, "c": {"start": cut + 99}}
    got = tool.regime_split(sessions, {"a": 3, "b": 4, "c": 5})
    assert got == {"before the judge": {"a": 3}, "judge live": {"b": 4, "c": 5}}
