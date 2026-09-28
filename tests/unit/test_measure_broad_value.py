"""``tools/measure_broad_value.py`` over synthetic transcripts and vaults
shaped like the real ones (#527)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_broad_value as tool  # noqa: E402

from mnemo.core import llm  # noqa: E402

CWD = "/Users/you/github/app"
START = ("mnemo://v1 project=app\nlocal: [git]\n\n[last-briefing session=s0 date=2026-09-20]\n"
         "# Briefing\nWe agreed the release goes out on Fridays only.\n[/last-briefing]\n\n"
         "[mnemo learned since your last session]\n• app__no-emoji — No emoji in commits\n[/mnemo learned]")
REFLEX_0 = "mnemo reflex context:\n• [[yarn-only]]: use yarn (call read_mnemo_rule if you need the full file)."
REFLEX_1 = ("mnemo reflex context:\n• [[app__pin-node]]: pin node 20 (call read_mnemo_rule …).\n"
            "• [[app__small-prs]]: keep PRs small (call read_mnemo_rule …).")


def _user(text, ts, *, uuid="u"):
    return {"type": "user", "timestamp": ts, "cwd": CWD, "entrypoint": "cli", "uuid": uuid,
            "message": {"role": "user", "content": text}}


def _agent(text, ts, *tools, model="claude-opus-5-5"):
    content = [{"type": "text", "text": text}] + list(tools)
    return {"type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "model": model, "content": content}}


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


LISTING = json.dumps([{"slug": "app__lint-first", "type": "feedback"},
                      {"slug": "app__other", "type": "reference"}])


def _session():
    return [
        _hook("SessionStart:startup", START, "some other plugin's context"),
        _instructions(("/Users/you/github/app/CLAUDE.md", "Use yarn."),
                      ("/Users/you/.claude/projects/app/memory/MEMORY.md", "- a note")),
        _user("install the deps and run the tests", "2026-09-21T10:00:00Z", uuid="p0"),
        _hook("UserPromptSubmit", REFLEX_0),
        _agent("Running npm install.", "2026-09-21T10:00:05Z",
               _tool_use("t1", "mcp__plugin_mnemo_mnemo__read_mnemo_rule", slug="app__pin-node"),
               _tool_use("t2", "mcp__mnemo__list_rules_by_topic", topic="git")),
        _tool_result("t1", "# pin node\nPin node 20.", "2026-09-21T10:00:06Z"),
        _tool_result("t2", LISTING, "2026-09-21T10:00:06Z"),
        _agent("Done, all green.", "2026-09-21T10:00:07Z", model="<synthetic>"),
        _user("now open the PR", "2026-09-21T10:01:00Z", uuid="p1"),
        _hook("UserPromptSubmit", REFLEX_1),
        _agent("Opened.", "2026-09-21T10:01:05Z", model="claude-fable-5-1"),
    ]


def _row(slug, prompts, **channels):
    row = {"session_id": "s1", "slug": slug, "prompts": prompts, "new": True, "strict": False,
           "session_start": False, "reflex": False, "mcp": False}
    row.update(channels)
    return row


# --- what the session had in context --------------------------------------------------

def test_context_at_records_everything_before_the_prompt_was_answered():
    first = tool.context_at(_session(), 0)
    assert first["text"] == "install the deps and run the tests" and first["model"] == "claude-opus-5-5"
    assert first["session_start"] == [START]
    assert [p for p, _ in first["native"]] == ["/Users/you/.claude/projects/app/memory/MEMORY.md",
                                               "/Users/you/github/app/CLAUDE.md"]
    # the prompt's own reflex block is its own; the MCP reads come after it
    assert first["reflex"] == [(0, REFLEX_0)] and first["mcp"] == []
    second = tool.context_at(_session(), 1)
    assert second["answered"].startswith("Running npm install.") and "Done, all green." in second["answered"]
    assert second["reflex"] == [(0, REFLEX_0), (1, REFLEX_1)]
    assert [c["tool"] for c in second["mcp"]] == ["read_mnemo_rule", "list_rules_by_topic"]
    assert second["model"] == "claude-fable-5-1"
    assert tool.context_at(_session(), 5) is None


def test_place_takes_the_first_relevant_prompt_with_the_rule_in_context():
    events = _session()
    # relevant at both prompts, but reflexed only with the second
    assert tool.place(_row("app__small-prs", [0, 1]), events, "app")["i"] == 1
    # a pre-rename slug in the transcript still names today's rule
    assert tool.place(_row("app__yarn-only", [0, 1]), events, "app")["i"] == 0
    # read by MCP after prompt 0: in context at prompt 1
    assert tool.place(_row("app__pin-node", [0, 1]), events, "app")["i"] == 1
    # #520's delivery judge found the envelope saying it: in context from the start
    assert tool.place(_row("app__fridays", [0, 1], session_start=True), events, "app")["i"] == 0


# --- the two arms -------------------------------------------------------------------------

def test_reflex_arm_without_drops_the_rules_line_and_keeps_the_others():
    events = _session()
    row = _row("app__small-prs", [1], reflex=True)
    arms = tool.build_arms(row, tool.place(row, events, "app"), "app", [])
    assert arms["measurable"] and arms["carriers"] == {"session_start": False, "reflex": True, "mcp": False}
    assert "[[app__small-prs]]" in arms["with"] and "[[app__small-prs]]" not in arms["without"]
    assert "[[app__pin-node]]" in arms["with"] and "[[app__pin-node]]" in arms["without"]
    # everything else is the same in both: the envelope, Claude Code's own files, the turn
    for arm in (arms["with"], arms["without"]):
        assert START in arm and "Use yarn." in arm and "- a note" in arm
        assert "Running npm install." in arm and "now open the PR" in arm
    assert arms["with"].replace("\n• [[app__small-prs]]: keep PRs small (call read_mnemo_rule …).", "") \
        == arms["without"]
    # the judge's CLAUDE.md is the repo's file, not the auto-memory index
    assert "Use yarn." in arms["claude_md"] and "- a note" not in arms["claude_md"]
    assert arms["model"] == "claude-fable-5-1" and arms["model_from"] == "transcript"


def test_a_block_left_with_its_header_alone_is_dropped():
    events = _session()
    row = _row("app__yarn-only", [0], reflex=True)
    arms = tool.build_arms(row, tool.place(row, events, "app"), "app", [])
    assert "mnemo reflex context" in arms["with"] and "mnemo reflex context" not in arms["without"]


def test_mcp_arm_without_drops_the_read_and_the_listing_entry():
    events = _session()
    read = _row("app__pin-node", [1], mcp=True, reflex=True)
    arms = tool.build_arms(read, tool.place(read, events, "app"), "app", [])
    assert "Pin node 20." in arms["with"] and "Pin node 20." not in arms["without"]
    assert "[[app__pin-node]]" not in arms["without"]
    listed = _row("app__lint-first", [1], mcp=True)
    arms = tool.build_arms(listed, tool.place(listed, events, "app"), "app", [])
    assert "app__lint-first" in arms["with"] and "app__lint-first" not in arms["without"]
    assert "app__other" in arms["without"]


def test_strip_mcp_drops_a_listing_of_the_rule_alone():
    call = {"tool": "list_rules_by_topic", "input": {}, "text": json.dumps([{"slug": "x"}])}
    assert tool.strip_mcp(call, "app__x", "app") is None
    prose = {"tool": "list_rules_by_topic", "input": {}, "text": "x — do it\ny — other"}
    assert tool.strip_mcp(prose, "x", "app")["text"] == "y — other"


def test_envelope_rule_is_removed_by_name_and_by_the_located_lines():
    events = _session()
    named = _row("app__no-emoji", [0], session_start=True)
    arms = tool.build_arms(named, tool.place(named, events, "app"), "app", [])
    assert "app__no-emoji" in arms["with"] and "app__no-emoji" not in arms["without"]
    assert "Fridays" in arms["without"]
    lines = tool.envelope_lines([START])
    friday = next(n for n, ln in enumerate(lines, 1) if "Fridays" in ln)
    judged = _row("app__fridays", [0], session_start=True)
    arms = tool.build_arms(judged, tool.place(judged, events, "app"), "app", [friday])
    assert "Fridays" in arms["with"] and "Fridays" not in arms["without"]
    assert arms["carriers"]["session_start"] and arms["dropped_lines"] == [friday]
    # the locator found nothing: nothing to remove, so the unit is not measurable
    arms = tool.build_arms(judged, tool.place(judged, events, "app"), "app", [])
    assert not arms["measurable"]


def test_locate_prompt_numbers_the_envelope_and_parse_keeps_valid_lines():
    prompt = tool.locate_prompt("Release on Fridays.", [START])
    assert "1: mnemo://v1 project=app" in prompt
    n = len(tool.envelope_lines([START]))
    assert tool.parse_locate('{"lines": [2, "3", 999, "x"]}', n) == [2, 3]
    assert tool.parse_locate("not json", n) is None
    assert tool.parse_locate('{"lines": []}', n) == []


# --- the judge -----------------------------------------------------------------------------

def test_mask_hides_the_slug_its_short_form_and_wikilinks():
    masked, hit = tool.mask("see [[app__small-prs]], small-prs and app__small-prs via read_mnemo_rule",
                            "app__small-prs")
    assert hit and "small-prs" not in masked and "read_mnemo_rule" not in masked


def test_order_is_seeded_per_comparison_and_flipped_for_the_second_rater():
    cids = [tool.comparison_id("u%d" % n, k) for n in range(40) for k in range(2)]
    firsts = [tool.with_first(c, 0) for c in cids]
    assert 0 < sum(firsts) < len(firsts)
    assert all(tool.with_first(c, 1) != tool.with_first(c, 0) for c in cids)
    for c in cids[:6]:
        for ri in (0, 1):
            one_is_with = tool.with_first(c, ri)
            got = tool.parse_judge('{"better": "1", "why": "w"}', c, ri)
            assert got == {"better": tool.WITH if one_is_with else tool.WITHOUT, "why": "w"}
            assert tool.parse_judge('{"better": "tie"}', c, ri)["better"] == tool.TIE
    assert tool.parse_judge('{"better": "3"}', cids[0], 0) is None


def test_the_rater_never_sees_the_rule_or_which_reply_is_which():
    events = _session()
    row = _row("app__small-prs", [1], reflex=True)
    arms = tool.build_arms(row, tool.place(row, events, "app"), "app", [])
    answers = {tool.WITH: [{"text": "Per [[app__small-prs]] I split it."}], tool.WITHOUT: [{"text": "One PR."}]}
    for ri in (0, 1):
        prompt = tool.comparison_prompt(arms, answers, 0, ri)
        assert "keep PRs small" not in prompt and "small-prs" not in prompt
        assert "Use yarn." in prompt and "now open the PR" in prompt and "Running npm install." in prompt
        assert "with" not in prompt.lower().split("## reply 1")[1].split("\n")[0]
    assert tool.comparison_prompt(arms, answers, 1, 0) is None


# --- the numbers ----------------------------------------------------------------------------

def _v(better):
    return {"better": better, "why": ""}


def test_both_raters_must_agree_or_it_is_a_tie():
    verdicts = {"m1": {"u|0": _v("with"), "u|1": _v("with")},
                "m2": {"u|0": _v("with"), "u|1": _v("without")}}
    assert tool.unit_h(verdicts, "u", ["m1", "m2"]) == 0.5
    assert tool.unit_h(verdicts, "u", ["m1"]) == 1.0
    assert tool.unit_h(verdicts, "u", ["m2"]) == 0.0
    assert tool.unit_h({"m1": {"u|0": _v("with")}}, "u", ["m1"]) is None
    shares = tool.outcome_shares(verdicts, ["u"], ["m1", "m2"])
    assert (shares["with"], shares["tie"], shares["without"], shares["comparisons"]) == (1, 1, 0, 2)


def test_combine_is_rate_times_mean_h_and_counts_unmeasured_units_in_the_rate():
    rows = tool.per_session_rows(["a", "b", "c", "d"], {"a": ["u1", "u2"], "b": ["u3"]},
                                 {"u1": 1.0, "u2": None, "u3": 0.0})
    assert rows == [{"new": 2, "h": [1.0]}, {"new": 1, "h": [0.0]}, {"new": 0, "h": []}, {"new": 0, "h": []}]
    st = tool.combine(rows, n_boot=200)
    assert st["rate"] == 0.75 and st["h"] == 0.5 and st["E"] == 0.375 and st["measured"] == 2
    assert st["ci"]["E"][0] <= st["E"] <= st["ci"]["E"][1]
    assert tool.combine([{"new": 1, "h": []}])["measured"] == 0


def test_verdict_uses_the_preset_bar():
    def st(E, lo, hi, h_hi=0.5):
        return {"E": E, "ci": {"E": [lo, hi], "h": [-0.1, h_hi]}}
    assert tool.verdict(st(0.1, 0.01, 0.2)) == "positive"
    assert tool.verdict(st(0.02, -0.01, 0.05)) == "null"
    assert tool.verdict(st(-0.05, -0.09, -0.01, h_hi=-0.02)) == "negative"
    assert tool.verdict(st(0.05, -0.01, 0.12)) == "inconclusive"
    assert tool.verdict(st(0.1, 0.0, 0.2)) == "inconclusive"
    assert tool.verdict({}) == "no estimate yet"


def test_audit_label_is_junk_only_when_both_raters_say_generic_or_narrative(tmp_vault):
    base = tmp_vault / ".mnemo" / "audit-2026-09-22"
    base.mkdir(parents=True)
    (base / "key.json").write_text(json.dumps({"a1": "yarn-only", "a2": "pin-node", "a3": "x"}), encoding="utf-8")
    (base / "labels-A.json").write_text(json.dumps({"a1": {"cat": "G"}, "a2": {"cat": "N"}, "a3": {"cat": "G"}}),
                                        encoding="utf-8")
    (base / "labels-B.json").write_text(json.dumps({"a1": {"cat": "N"}, "a2": {"cat": "T"}}), encoding="utf-8")
    assert tool.audit_labels(tmp_vault) == {"yarn-only": "junk", "pin-node": "other"}


# --- end to end --------------------------------------------------------------------------------

def _resp(text):
    return llm.LLMResponse(text=text, total_cost_usd=0.25, input_tokens=10, output_tokens=2,
                           api_key_source=None, raw={})


def _setup(tmp_path):
    vault = tmp_path / "vault"
    for page_type, slug, body in (("feedback", "app__small-prs", "Keep PRs small."),
                                  ("reference", "app__fridays", "Release on Fridays only.")):
        path = vault / "shared" / page_type / (slug + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nname: %s\ndescription: d\n---\n%s\n" % (slug, body), encoding="utf-8")
    transcript = tmp_path / "s1.jsonl"
    transcript.write_text("\n".join(json.dumps(e) for e in _session()) + "\n", encoding="utf-8")
    units = {"since": "2026-09-01", "rated": ["s1", "s2"],
             "sessions": {"s1": {"path": str(transcript), "project": "app", "cwd": CWD, "start": 1758448800.0},
                          "s2": {"path": str(transcript), "project": "app", "cwd": CWD, "start": 1758448800.0}},
             "columns": {"both": [_row("app__small-prs", [1], reflex=True),
                                  dict(_row("app__fridays", [0, 1], session_start=True), session_id="s2"),
                                  dict(_row("app__gone", [0]), new=False)]}}
    units_file = tmp_path / "units.json"
    units_file.write_text(json.dumps(units), encoding="utf-8")
    return vault, units_file


def test_main_locates_answers_judges_then_reports_and_a_rerun_asks_nothing(tmp_path, capsys, monkeypatch):
    vault, units_file = _setup(tmp_path)
    monkeypatch.chdir(tmp_path)
    base = ["--vault", str(vault), "--units", str(units_file), "--out", str(tmp_path / "out"),
            "--rater", "m1", "--rater", "m2", "--pause", "0", "--workers", "1"]
    monkeypatch.setattr(llm, "resolve", lambda cfg: pytest.fail("a dry run must not resolve a provider"))
    assert tool.main(base + ["--json"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert (data["units"], data["placed"], data["measurable"], data["judged"]) == (2, 2, 1, 0)
    assert "m1: pending 1 locate" in captured.err

    asked = []

    def provider(prompt, *, system, model, timeout):
        asked.append((model, system))
        if system == tool.LOCATE_SYSTEM:
            n = next(ln.split(":")[0] for ln in prompt.splitlines() if "Fridays only." in ln and ln[0].isdigit())
            return _resp(json.dumps({"lines": [int(n)]}))
        if system == tool.rl.ARM_SYSTEM:
            # a reply that acts on every rule in its context
            return _resp(" ".join(w for w, cue in (("small", "keep PRs small"), ("friday", "Fridays only"))
                                  if cue in prompt) or "anything")
        assert system == tool.JUDGE_SYSTEM
        assert "Keep PRs small." not in prompt and "Release on Fridays" not in prompt
        one, two = (sum(w in ("small", "friday") for w in r.split())
                    for r in prompt.split("## Reply 1")[1].split("## Reply 2"))
        return _resp(json.dumps({"better": "1" if one > two else "2" if two > one else "tie", "why": "fits"}))
    monkeypatch.setattr(llm, "resolve", lambda cfg: provider)
    assert tool.main(base + ["--send", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    systems = [s for _, s in asked]
    assert systems.count(tool.LOCATE_SYSTEM) == 2 and systems.count(tool.JUDGE_SYSTEM) == 8
    assert systems.count(tool.rl.ARM_SYSTEM) == 8
    # each unit answered with its session's model
    assert {m for m, s in asked if s == tool.rl.ARM_SYSTEM} == {"claude-fable-5-1", "claude-opus-5-5"}
    both = data["results"]["both"]
    assert (data["measurable"], data["judged"]) == (2, 2)
    # 2 new units over 2 rated sessions, each better with the rule every time
    assert (both["rate"], both["h"], both["E"]) == (1.0, 1.0, 1.0)
    assert data["verdict"] == "positive" and data["shares"]["share_with"] == 1.0
    assert set(data["breakdowns"]["page type"]) == {"feedback", "reference"}
    assert data["cost"]["m1"]["calls"] == 5 and len(data["examples"]["wins"]) == 2

    asked.clear()
    assert tool.main(base + ["--send", "--json"]) == 0
    assert asked == [] and json.loads(capsys.readouterr().out)["judged"] == 2
    assert "VERDICT" not in capsys.readouterr().out
    assert tool.main(base) == 0
    assert "VERDICT (both raters): POSITIVE" in capsys.readouterr().out


def test_an_envelope_that_arrives_later_places_the_unit_where_it_is_in_context():
    later = "mnemo://v1 project=app\n[mnemo learned since your last session]\n• app__late — Late\n[/mnemo learned]"
    events = _session() + [
        _hook("SessionStart:compact", later),
        _user("and tag the release", "2026-09-21T10:05:00Z", uuid="p2"),
        _agent("Tagged.", "2026-09-21T10:05:05Z"),
    ]
    # named by the later envelope only
    assert tool.place(_row("app__late", [0, 1, 2], session_start=True), events, "app")["i"] == 2
    # said by content (#520's judge read every envelope): the first prompt with all of them
    assert tool.place(_row("app__fridays", [0, 2], session_start=True), events, "app")["i"] == 2


def test_units_waiting_for_520s_judge_wait_and_a_changed_outcome_is_rebuilt(tmp_path, capsys, monkeypatch):
    vault, units_file = _setup(tmp_path)
    units = json.loads(units_file.read_text(encoding="utf-8"))
    units["columns"]["both"].append(dict(_row("app__pin-node", [1], mcp=True), judged=False))
    units_file.write_text(json.dumps(units), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    base = ["--vault", str(vault), "--units", str(units_file), "--out", str(tmp_path / "out"),
            "--rater", "m1", "--rater", "m2", "--pause", "0", "--workers", "1", "--json"]
    asked = []

    def provider(prompt, *, system, model, timeout):
        asked.append(system)
        if system == tool.LOCATE_SYSTEM:
            return _resp('{"lines": []}')
        if system == tool.rl.ARM_SYSTEM:
            return _resp("reply %d" % len(prompt))
        return _resp('{"better": "tie", "why": "same"}')
    monkeypatch.setattr(llm, "resolve", lambda cfg: provider)
    assert tool.main(base + ["--send"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["provisional"] == 1 and data["units"] == 2
    assert asked.count(tool.rl.ARM_SYSTEM) == 4  # small-prs only: fridays has no located line

    # #520 now says small-prs also came by MCP: its arms, answers and verdicts are rebuilt
    units["columns"]["both"][0]["mcp"] = True
    units_file.write_text(json.dumps(units), encoding="utf-8")
    asked.clear()
    assert tool.main(base + ["--send"]) == 0
    capsys.readouterr()
    assert asked.count(tool.rl.ARM_SYSTEM) == 4 and asked.count(tool.JUDGE_SYSTEM) == 4
    asked.clear()
    assert tool.main(base + ["--send"]) == 0
    assert asked == []
