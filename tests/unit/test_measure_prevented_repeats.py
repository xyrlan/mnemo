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
    # every unit and its outcome, for #527
    units = json.loads((tmp_path / "out" / tool.UNITS_NAME).read_text(encoding="utf-8"))
    assert units["rated"] == ["later002-x"] and units["sessions"]["later002-x"]["project"] == "app"
    (row,) = units["columns"]["both"]
    assert (row["session_id"], row["slug"], row["prompts"], row["new"], row["reflex"]) == (
        "later002-x", "app__yarn-only", [0], True, True)

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


# --- reflex blocks, read by entry (#545) ------------------------------------------------------

OLD_BLOCK = ("mnemo reflex context:\n"
             "• [[app__pin-node]]: pin node 20, see [[app__nvmrc]] (call read_mnemo_rule if you need the full file).\n"
             "• [[Keep a title with spaces]]: an old page title (call read_mnemo_rule if you need the full file).\n"
             "• [[app__small-prs]]: keep PRs small (call read_mnemo_rule if you need the full file).")


def _full_block(*bodies):
    from mnemo.core.reflex import render

    entries = [render.Entry(slug, body.split("\n")[0], body, "/v/%s.md" % slug) for slug, body in bodies]
    return render.render(entries).text


def test_full_blocks_deliver_their_heads_not_the_links_in_their_bodies():
    block = _full_block(("app__pin-node", "Pin node 20.\n\nSee [[app__nvmrc]] and [[app__ci-matrix]].\n"
                                          "- a list line\n• not a head: a bullet without a link"),
                        ("app__small-prs", "Keep PRs small; [[app__pin-node]] says why."))
    assert tool.is_full_block(block)
    assert tool.reflex_slugs(block) == ["app__pin-node", "app__small-prs"]
    entries = tool.reflex_entries(block)
    assert [s for s, _ in entries] == ["app__pin-node", "app__small-prs"]
    # an entry runs from its head through the line before the next head
    assert entries[0][1].startswith("• [[app__pin-node]]:\nPin node 20.") and entries[0][1].endswith(
        "• not a head: a bullet without a link")
    assert "\n".join(["mnemo reflex context:"] + [t for _, t in entries]) == block


def test_a_cut_entry_and_a_preview_line_in_a_full_block_are_entries_too():
    from mnemo.core.reflex import render

    long_body = "\n".join("line %d with [[app__link-%d]]" % (n, n) for n in range(600))
    rendered = render.render([render.Entry("app__big", "line 0", long_body, "/v/big.md"),
                              render.Entry("app__gone", "the preview", None),
                              render.Entry("app__small", "tiny", "tiny")])
    assert rendered.rule_whole == [False, False, True]
    assert tool.reflex_slugs(rendered.text) == ["app__big", "app__gone", "app__small"]


def test_old_blocks_read_as_they_always_did():
    # every [[…]] in a one-line block, a preview's link too, and no title with spaces:
    # the frozen #520, #527 and #535 readings rest on this
    assert not tool.is_full_block(OLD_BLOCK)
    assert tool.reflex_slugs(OLD_BLOCK) == tool._WIKI.findall(OLD_BLOCK) == [
        "app__pin-node", "app__nvmrc", "app__small-prs"]
    assert [s for s, _ in tool.reflex_entries(OLD_BLOCK)] == ["app__pin-node", "", "app__small-prs"]


def test_no_rendered_body_line_can_pass_for_a_head():
    # heads are unambiguous because no body line starts with "• [[" (the whole
    # vault had none on 2026-09-28): a body line that did would split its entry
    from mnemo.core.reflex import render

    for body in ("• a bullet\n  • [[app__x]]: indented", "text [[app__x]]: inline", "•[[app__x]]: no space"):
        text = render.render([render.Entry("app__r", "p", body)]).text
        assert tool.reflex_slugs(text) == ["app__r"]
    split = render.render([render.Entry("app__r", "p", "• [[app__x]]: a head-like body line")]).text
    assert tool.reflex_slugs(split) == ["app__r", "app__x"]


def test_walk_counts_a_full_blocks_heads_only():
    block = _full_block(("app__pin-node", "Pin node 20; see [[app__nvmrc]]."))
    events = [_user("set up node for me please", "2026-09-29T10:00:00Z", uuid="p0"),
              _hook("UserPromptSubmit", block),
              _agent("Done.", "2026-09-29T10:00:05Z")]
    (p,) = tool.walk(events)["prompts"]
    assert p["reflex"] == ["app__pin-node"]


def test_collect_sessions_reads_a_timestamp_bound_by_the_instant(tmp_path):
    projects, vault = tmp_path / "projects", tmp_path / "vault"
    proj = projects / "-Users-you-github-app"
    proj.mkdir(parents=True)
    (vault / "bots").mkdir(parents=True)
    for sid, ts in (("early001-x", "2026-09-28T22:00:00Z"), ("fresh002-x", "2026-09-28T23:30:00Z"),
                    ("later003-x", "2026-10-02T09:00:00Z")):
        events = [_user("please look at the build for me", ts, uuid="p" + sid), _agent("Ok.", ts)]
        (proj / (sid + ".jsonl")).write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    assert sorted(tool.collect_sessions(projects, vault, "2026-09-28")) == [
        "early001-x", "fresh002-x", "later003-x"]
    assert sorted(tool.collect_sessions(projects, vault, "2026-09-28T23:09:00Z")) == ["fresh002-x", "later003-x"]
    assert sorted(tool.collect_sessions(projects, vault, "2026-09-28T23:09:00Z",
                                        "2026-10-01T00:00:00Z")) == ["fresh002-x"]


# --- #565: Text B as what Claude Code loaded ----------------------------------------------

def test_the_default_text_b_still_adds_the_closest_note_bodies():
    files = [("CLAUDE.md", "use yarn"), ("MEMORY.md", "- [Pin](pin.md) — pin node")]
    notes = [("pin.md", "Pin node 20 because CI breaks on 18")]
    text = tool.native_text(files, notes)
    assert "### memory note pin.md" in text and "CI breaks on 18" in text
    assert tool.native_text(files, notes) == tool.native_text(files, notes, loaded=False)


def test_loaded_text_b_drops_note_bodies_and_cuts_memory_md_where_claude_code_does():
    index = "\n".join("- [n%d](n%d.md) — line %d" % (i, i, i) for i in range(250))
    files = [("CLAUDE.md", "use yarn"), ("/home/you/.claude/projects/p/memory/MEMORY.md", index)]
    text = tool.native_text(files, [("n1.md", "the body of note one")], loaded=True)
    assert "the body of note one" not in text and "memory note" not in text
    assert "line 199" in text and "line 200" not in text and "use yarn" in text


def test_a_recorded_memory_md_already_cut_by_claude_code_is_left_as_it_is():
    recorded = "\n".join("- line %d" % i for i in range(200)) + "\n\n> WARNING: MEMORY.md is 250 lines"
    assert tool.loaded_memory(recorded) == recorded
    assert tool.loaded_memory("short") == "short"


def test_the_batch_prompt_passes_the_reading_through():
    unit = {"rule": "R", "ss": "start", "files": [("MEMORY.md", "idx")], "notes": [("n.md", "NOTE BODY")]}
    assert "NOTE BODY" in tool.batch_prompt([unit])
    assert "NOTE BODY" not in tool.batch_prompt([unit], loaded=True)
