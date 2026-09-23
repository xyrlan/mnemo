"""``tools/measure_day_one.py`` over synthetic transcripts.

No model is called: ``core.llm.call`` is replaced by a stub that answers each
helper by its system prompt. What is pinned: the SessionEnd schedule the
replay follows (debounce on the transcript's clock, extraction before
briefing), the install run's selection, the rates and headline, the budget
meter, isolation of the real vault and config, and that a rerun resumes
without spending again.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_day_one.py"
_spec = importlib.util.spec_from_file_location("measure_day_one", _TOOL)
day = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = day  # a dataclass resolves its annotations through it
_spec.loader.exec_module(day)

T0 = datetime(2026, 8, 1, 9, 0, 0)


def _s(sid, start_min, *, end_min=None, mutations=1, prompts=1, mtime=None):
    start = (T0 + timedelta(minutes=start_min)).timestamp()
    end = (T0 + timedelta(minutes=end_min if end_min is not None else start_min + 30)).timestamp()
    return day.Session(sid=sid, path=Path(sid + ".jsonl"), start=start, end=end,
                       mutations=mutations,
                       prompts=[(start + i, "prompt %d" % i) for i in range(prompts)],
                       mtime=mtime if mtime is not None else end)


# --- the schedule ------------------------------------------------------------------

def test_the_first_session_end_always_extracts_and_its_own_briefing_waits_for_the_next():
    rows = day.plan_b([_s("a", 0), _s("b", 120)], min_interval_min=60, min_new=1,
                      min_mutations=1, chunk=10)

    assert rows[0]["extract"] and rows[0]["consumed"] == 0 and rows[0]["worst"] == 1
    assert rows[1]["extract"] and rows[1]["consumed"] == 1
    assert rows[1]["worst"] == 2 + 1  # one chunk, its gate call, the briefing


def test_extraction_is_debounced_on_the_transcripts_clock_not_the_replays():
    sessions = [_s("a", 0), _s("b", 40, end_min=50), _s("c", 200)]

    rows = day.plan_b(sessions, min_interval_min=60, min_new=1, min_mutations=1, chunk=10)

    assert [r["extract"] for r in rows] == [True, False, True]
    assert rows[2]["consumed"] == 2


def test_a_session_without_file_mutations_is_not_briefed_and_feeds_no_extraction():
    sessions = [_s("a", 0, mutations=0), _s("b", 120, mutations=0)]

    rows = day.plan_b(sessions, min_interval_min=60, min_new=1, min_mutations=1, chunk=10)

    assert [r["briefing"] for r in rows] == [False, False]
    assert rows[1]["extract"] is False and rows[1]["worst"] == 0


def test_the_install_run_takes_the_newest_by_mtime_before_the_mutation_gate():
    history = [_s("old", 0, mtime=1), _s("quiet", 10, mutations=0, mtime=3), _s("new", 20, mtime=2)]

    chosen = day.install_selection(history, cap=2, min_mutations=1)

    assert [s.sid for s in chosen] == ["new"]


def test_next_prompts_cross_sessions_and_first_turns_skip_empty_ones():
    future = [_s("a", 0, prompts=2), _s("e", 10, prompts=0), _s("b", 20, prompts=3)]

    assert [(s.sid, t) for s, _, t in day.first_prompts(future, 3)] == [
        ("a", "prompt 0"), ("a", "prompt 1"), ("b", "prompt 0")]
    assert [s.sid for s, _, _ in day.first_turns(future, 5)] == ["a", "b"]


# --- the numbers -------------------------------------------------------------------

def _unit(uid, slugs):
    return {"uid": uid, "pool": [{"slug": s, "text": "t"} for s in slugs]}


def test_rates_count_prompts_pairs_and_leave_unlabelled_pairs_unguessed():
    units = [_unit("u1", ["r1", "r2"]), _unit("u2", []), _unit("u3", ["r3"])]
    labels = {"u1": {"r1": 2, "r2": 0}}

    r = day.rates(units, labels)

    assert (r["prompts"], r["fired"], r["pairs"], r["on_point"], r["labelled"],
            r["prompts_on_point"]) == (3, 2, 3, 1, 2, 1)


def test_the_headline_is_the_first_session_with_an_on_point_pair():
    assert day.first_on_point([{"on_point": 0}, {"on_point": 0}, {"on_point": 1}]) == 3
    assert day.first_on_point([{"on_point": 0}]) is None


def test_the_first_run_invitation_alone_carries_nothing():
    assert not day.carries({"first_run": "[mnemo] first run: ...", "local_topics": []})
    assert day.carries({"last_briefing": True})
    assert day.carries({"staged_notice": "[mnemo] 3 staged"})


def test_label_calls_never_split_a_bound_below_one_call():
    assert day.label_calls(0) == 0
    assert day.label_calls(1) == 1
    assert day.label_calls(41) == 2
    assert day.max_pairs(prompts=30, cap=10) == 10
    assert day.max_pairs(prompts=2, cap=10) == 4


def test_real_vault_changes_are_listed_and_the_ones_naming_the_run_flagged(tmp_path):
    (tmp_path / "keep.md").write_text("a", encoding="utf-8")
    before = day.fingerprint(tmp_path)
    (tmp_path / "bots" / "clubinho").mkdir(parents=True)
    (tmp_path / "bots" / "clubinho" / "log.md").write_text("b", encoding="utf-8")
    (tmp_path / "other.md").write_text("c", encoding="utf-8")

    diff = day.changed(before, day.fingerprint(tmp_path))

    assert diff == ["bots/clubinho/log.md", "other.md"]
    assert day.suspects(diff, ["clubinho/"]) == ["bots/clubinho/log.md"]


# --- the meter ---------------------------------------------------------------------

def test_the_meter_counts_every_call_and_refuses_past_the_budget(monkeypatch):
    from mnemo.core import llm

    monkeypatch.setattr(llm, "call", lambda prompt, **kw: llm.LLMResponse(
        text="ok", total_cost_usd=0.5, input_tokens=1, output_tokens=1, api_key_source="none", raw={}))
    meter = day.Meter(budget=2)

    with meter.installed():
        llm.call("x", system=None, model="m")
        llm.call("y", system=None, model="m")
        with pytest.raises(llm.LLMSubprocessError):
            llm.call("z", system=None, model="m")

    assert (meter.calls, meter.usd, meter.by_model) == (2, 1.0, {"m": 2})
    assert llm.call("after", system=None, model="m").text == "ok"


# --- end to end, stubbed model -----------------------------------------------------

WORDS = "rotate the ledger cache before replaying payment webhooks"


def _transcript(path, cwd, start, prompts, *, edits=1):
    lines = []
    t = start
    for text in prompts:
        lines.append({"type": "user", "cwd": cwd, "sessionId": path.stem,
                      "timestamp": t.isoformat() + "Z",
                      "message": {"role": "user", "content": text}})
        t += timedelta(minutes=5)
        lines.append({"type": "assistant", "cwd": cwd, "timestamp": t.isoformat() + "Z",
                      "message": {"role": "assistant", "content": [
                          {"type": "tool_use", "name": "Edit", "id": "x", "input": {}}] * edits}})
        t += timedelta(minutes=5)
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def _stub(calls):
    """Answer each helper by its system prompt, the way the real ones are told apart."""
    import re

    from mnemo.core import llm
    from mnemo.core.extract import prompts, reference_gate

    def call(prompt, *, system=None, model="", timeout=60):
        calls.append(model)
        if system == prompts.HARVEST_SYSTEM_PROMPT:
            text = json.dumps({"pages": [{"slug": "harvested-%d" % len(calls), "type": "feedback",
                                          "name": "harvested", "description": WORDS,
                                          "body": WORDS}]})
        elif system == prompts.BRIEFING_SYSTEM_PROMPT:
            text = "# Briefing\n\n" + WORDS + "\n"
        elif system == reference_gate.SYSTEM_PROMPT:
            text = json.dumps({"verdicts": [{"i": i, "cat": "S"} for i in range(1, 20)]})
        elif system == day.mrr.RATER_SYSTEM:
            text = json.dumps({str(i): 2 for i in range(1, 100)})
        else:
            files = re.findall(r"<<<FILE: ([^>]+)>>>", prompt)
            text = json.dumps({"pages": [
                {"slug": "rule-%d" % (n + len(calls)), "type": "user", "name": "ledger cache rule",
                 "description": WORDS, "body": WORDS, "source_files": [f]}
                for n, f in enumerate(files)]})
        return llm.LLMResponse(text=text, total_cost_usd=0.01, input_tokens=10,
                               output_tokens=10, api_key_source="none", raw={})

    return call


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "projects" / "-tmp-repo"
    root.mkdir(parents=True)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    for n in range(6):
        _transcript(root / ("s%d-0000-0000-0000-000000000000.jsonl" % n), str(repo),
                    T0 + timedelta(hours=3 * n),
                    ["please %s now, session %d" % (WORDS, n), "and check the " + WORDS])
    return root


def test_both_arms_run_isolated_and_a_rerun_spends_nothing(corpus, tmp_path, monkeypatch, capsys):
    from mnemo.core import llm

    calls = []
    monkeypatch.setattr(llm, "call", _stub(calls))
    monkeypatch.delenv("MNEMO_CONFIG_PATH", raising=False)
    real = Path.home() / "mnemo"
    real.mkdir()
    (real / "untouched.md").write_text("x", encoding="utf-8")
    work = tmp_path / "work"

    assert day.main(["--send", "--arm", "both", "--corpus", str(corpus), "--work", str(work),
                     "--install-after", "3", "--prompts", "4", "--budget", "60"]) == 0

    progress = json.loads((work / "progress.json").read_text(encoding="utf-8"))
    a, b = progress["a"], progress["b"]
    # The install run harvested the history; the harvest alone makes nothing
    # live. Since #471 the first extraction runs backfilled pages through the
    # normal gates, so what they clear goes live and the next prompts can
    # reach it — and with a live rule there is no staged-only notice.
    assert a["backfill"]["counts"]["memory"] == 3
    assert a["backfill"]["counts"]["live"] == 0
    assert a["extract"]["counts"]["live"] >= 1
    assert not a["extract"]["carry"]["staged_notice"]
    assert len(a["units"]) == 4 and any(u["pool"] for u in a["units"])
    # Arm (b): session 1 meets an empty vault; its briefing is consolidated at
    # session 2's end (extraction runs first), so session 3 is the first that
    # can be injected into.
    rows = b["rows"]
    assert len(rows) == 6
    assert rows[0]["counts"]["live"] == 0 and rows[0]["carry_next"]["last_briefing"]
    assert rows[1]["counts"]["live"] >= 1
    assert not any(u["pool"] for u in rows[0]["units"] + rows[1]["units"])
    assert any(u["pool"] for u in rows[2]["units"])
    # The report and the headline read the saved labels.
    data = day.report(progress, json.loads((work / "labels.json").read_text(encoding="utf-8"))[
        day.mrr.column(day.DEFAULT_RATER)], work)
    assert data["b"]["first_on_point"] == 3
    assert data["errors"] == {"a": {}, "b": {}}
    # Isolation: the config pointer is restored, the "real" vault is untouched,
    # and every call went through the meter.
    assert "MNEMO_CONFIG_PATH" not in os.environ
    assert sorted(p.name for p in real.iterdir()) == ["untouched.md"]
    assert progress["meta"]["real_vault"]["changed"] == 0
    assert progress["calls"]["total"] == len(calls)

    spent = len(calls)
    assert day.main(["--send", "--arm", "both", "--corpus", str(corpus), "--work", str(work)]) == 0
    assert len(calls) == spent
    out = capsys.readouterr().out
    assert "first on-point injection at session 3" in out


def test_arm_b_stops_before_a_session_the_budget_cannot_cover(corpus, tmp_path, monkeypatch):
    from mnemo.core import llm

    calls = []
    monkeypatch.setattr(llm, "call", _stub(calls))
    work = tmp_path / "work"

    assert day.main(["--send", "--arm", "b", "--corpus", str(corpus), "--work", str(work),
                     "--install-after", "3", "--budget", "6"]) == 0

    b = json.loads((work / "progress.json").read_text(encoding="utf-8"))["b"]
    assert len(b["rows"]) < 6 and b["stopped"].startswith("budget: session %d" % (len(b["rows"]) + 1))
    assert len(calls) <= 6


def test_the_dry_run_calls_nothing_and_prints_both_arms(corpus, tmp_path, monkeypatch, capsys):
    from mnemo.core import llm

    monkeypatch.setattr(llm, "call", lambda *a, **k: pytest.fail("the dry run called a model"))

    assert day.main(["--dry-run", "--corpus", str(corpus), "--work", str(tmp_path / "w")]) == 0

    out = capsys.readouterr().out
    assert "arm (a): 3 session(s) of history" in out and "arm (b): 6 of 6" in out
    assert not (tmp_path / "w").exists()


def test_a_briefing_that_fails_is_logged_and_the_session_goes_unbriefed(corpus, tmp_path, monkeypatch):
    from mnemo.core import llm
    from mnemo.core.extract import prompts

    calls = []
    inner = _stub(calls)

    def flaky(prompt, *, system=None, model="", timeout=60):
        if system == prompts.BRIEFING_SYSTEM_PROMPT and not calls:
            calls.append(model)
            raise llm.LLMTimeoutError("subprocess timed out twice after 60s")
        return inner(prompt, system=system, model=model, timeout=timeout)

    monkeypatch.setattr(llm, "call", flaky)
    work = tmp_path / "work"

    assert day.main(["--send", "--arm", "b", "--corpus", str(corpus), "--work", str(work),
                     "--install-after", "3"]) == 0

    progress = json.loads((work / "progress.json").read_text(encoding="utf-8"))
    rows = progress["b"]["rows"]
    assert len(rows) == 6 and rows[0]["briefed"] is False and rows[1]["briefed"] is True
    data = day.report(progress, {}, work)
    assert data["errors"]["b"] == {"briefing.cli:LLMTimeoutError": 1}


# --- arm (c): the auto-memory snapshot and its mirror -------------------------------

def _mem(name, mtype, body="a rule about " + WORDS):
    return ("---\nname: %s\ndescription: \"%s\"\nmetadata:\n  node_type: memory\n  type: %s\n---\n\n%s\n"
            % (name, body, mtype, body))


def _line(ts, **kw):
    return json.dumps(dict(timestamp=(T0 + timedelta(minutes=ts)).isoformat() + "Z", **kw))


def test_memory_events_read_writes_shell_edits_and_mentions(tmp_path):
    mem = tmp_path / "proj" / "memory"
    t = tmp_path / "s.jsonl"
    t.write_text("\n".join([
        _line(1, toolUseResult={"type": "create", "filePath": str(mem / "new.md"), "content": "x"}),
        _line(2, toolUseResult={"filePath": str(mem / "old.md"), "originalFile": "before",
                                "oldString": "b", "newString": "a"}),
        _line(3, message={"content": [{"type": "tool_use", "name": "Bash", "input": {
            "command": "cd %s/ && cat > shelled.md <<'EOF'\nx\nEOF" % mem}}]}),
        _line(4, message={"content": [{"type": "tool_use", "name": "Bash", "input": {
            "command": "cat %s/read.md 2>/dev/null" % mem}}]}),
        _line(5, message={"content": "unrelated"}),
    ]) + "\n", encoding="utf-8")

    events = day.memory_events([t], mem)

    assert [(e.name, e.kind, e.original) for e in events] == [
        ("new.md", "create", None), ("old.md", "edit", "before"),
        ("shelled.md", "shell", None), ("read.md", "mention", None)]


def test_memory_events_find_a_path_json_escaped_in_the_raw_line(tmp_path):
    # A Windows path's backslashes are escaped in the transcript's JSON, so
    # the raw line never holds the path as written; a quote is escaped the
    # same way and shows it on any platform.
    mem = tmp_path / 'we"ird' / "memory"
    t = tmp_path / "s.jsonl"
    t.write_text(_line(1, toolUseResult={"type": "create", "filePath": str(mem / "new.md")}) + "\n"
                 + _line(2, message={"content": "see %s" % (mem / "seen.md")}) + "\n",
                 encoding="utf-8")

    events = day.memory_events([t], mem)

    assert [(e.name, e.kind) for e in events] == [("new.md", "create"), ("seen.md", "mention")]


def test_the_snapshot_restores_what_it_can_and_names_what_it_cannot():
    at = 100.0
    E = day.MemEvent
    current = {
        "still.md": ("s", 50.0, 10.0),        # not modified since install
        "edited.md": ("new", 150.0, 10.0),    # Edit after install recorded the prior text
        "made.md": ("m", 150.0, 140.0),       # a Write created it after install
        "shelled.md": ("t", 150.0, 140.0),    # a shell wrote it, never named before
        "touched.md": ("u", 150.0, 150.0),    # named before install, shell-edited after
    }
    events = [E(120, "edited.md", "edit", "old"), E(130, "made.md", "create"),
              E(125, "shelled.md", "shell"), E(90, "touched.md", "mention"),
              E(140, "touched.md", "shell"), E(160, "touched.md", "edit", "u0"),
              E(80, "deleted.md", "edit", "x"), E(80, "README.md", "shell")]

    snap = day.snapshot_at(current, events, at)

    assert snap == {"still.md": ("s", "unchanged"), "edited.md": ("old", "restored"),
                    "made.md": (None, "created after"),
                    "shelled.md": (None, "created after (inferred)"),
                    "touched.md": ("u", "approximate"), "deleted.md": (None, "gone")}


def test_types_read_the_nested_metadata_and_only_non_project_types_cost_calls():
    types = day.memory_types([("a.md", _mem("a", "project")), ("b.md", _mem("b", "reference")),
                              ("c.md", "no frontmatter"), ("MEMORY.md", _mem("m", "user"))])

    assert types == {"project": 1, "reference": 1, "feedback": 1}
    assert day.extraction_bound({"project": 80, "feedback": 11, "user": 1}, chunk=10) == 2 * 2 + 2


def test_routes_name_why_each_page_is_live_or_staged(tmp_path):
    shared = tmp_path / "shared"

    def page(rel, extra=""):
        p = shared / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\nname: x\n%s---\n\nbody\n" % extra, encoding="utf-8")

    page("project/p.md")
    page("feedback/f.md")
    page("_inbox/project/b.md", "origin: backfill\n")
    page("_inbox/reference/d.md", "demoted_from: feedback\n")
    page("_inbox/reference/g.md", "reference_gate: generic\n")
    page("_inbox/feedback/m.md")
    page("_inbox/feedback/m.proposed.md")

    assert day.routes(tmp_path) == {
        "live: direct": {"project": 1}, "live: auto-promoted": {"feedback": 1},
        "staged: backfill origin": {"project": 1}, "staged: evidence demotion": {"reference": 1},
        "staged: reference gate": {"reference": 1}, "staged: multi-source/other": {"feedback": 1}}


@pytest.fixture
def memory(corpus):
    """The corpus's auto-memory: two files from before install, one after; and another project's."""
    mem = corpus / "memory"
    mem.mkdir()
    install = day.load_sessions(corpus)[3].start  # the first session after --install-after 3
    for name, mtype, when in (("ledger-cache.md", "project", install - 3600),
                              ("webhook-rule.md", "feedback", install - 3600),
                              ("later.md", "project", install + 3600)):
        (mem / name).write_text(_mem(name[:-3], mtype), encoding="utf-8")
        os.utime(str(mem / name), (when, when))
    # A worktree's session writes the same directory; its transcript lives elsewhere.
    (corpus.parent / "-tmp-repo-wt-1").mkdir()
    (corpus.parent / "-tmp-repo-wt-1" / "later.jsonl").write_text(json.dumps({
        "timestamp": datetime.fromtimestamp(install + 3600).astimezone().isoformat(),
        "toolUseResult": {"type": "create", "filePath": str(mem / "later.md")}}) + "\n",
        encoding="utf-8")
    other = corpus.parent / "-elsewhere" / "memory"
    other.mkdir(parents=True)
    (other / "secret.md").write_text(_mem("secret", "project"), encoding="utf-8")
    return mem


def test_arm_c_mirrors_the_install_snapshot_of_this_project_only(corpus, memory, tmp_path, monkeypatch, capsys):
    from mnemo.core import llm

    calls = []
    monkeypatch.setattr(llm, "call", _stub(calls))
    work = tmp_path / "work"

    assert day.main(["--send", "--corpus", str(corpus), "--work", str(work),
                     "--install-after", "3", "--prompts", "4"]) == 0

    progress = json.loads((work / "progress.json").read_text(encoding="utf-8"))
    snap = progress["snapshot"]
    assert snap["status"] == {"unchanged": 2, "created after": 1} and snap["present"] == 2
    c, ac = progress["c"], progress["ac"]
    assert c["mirror"]["counts"]["memory"] == 2 and c["mirror"]["memory_before"] == 0
    vault_c = work / "arm-c" / "vault"
    assert sorted(p.name for p in vault_c.glob("bots/*/memory/*.md")) == ["ledger-cache.md", "webhook-rule.md"]
    assert not list(vault_c.rglob("secret.md"))
    # The project page goes live without a model; the reflex can inject it.
    assert c["extract"]["routes"]["live: direct"] == {"project": 1}
    assert any(u["pool"] for u in c["units"])
    # (a)+(c): arm (a)'s harvested files, then the same mirror.
    assert ac["backfill_copied"] == progress["a"]["backfill"]["counts"]["memory"]
    assert ac["mirror"]["counts"]["memory"] == ac["backfill_copied"] + 2
    assert progress["calls_c"]["total"] >= 1 and progress["calls"]["total"] == len(calls)
    data = day.report(progress, json.loads((work / "labels.json").read_text(encoding="utf-8"))[
        day.mrr.column(day.DEFAULT_RATER)], work)
    assert data["c"]["next_prompts"]["fired"] >= 1 and data["c"]["next_prompts"]["labelled"] >= 1
    out = capsys.readouterr().out
    assert "ARM (c)" in out and "ARMS (a)+(c)" in out

    spent = len(calls)
    (memory / "ledger-cache.md").write_text("changed after the first run", encoding="utf-8")
    assert day.main(["--send", "--arm", "c", "--corpus", str(corpus), "--work", str(work)]) == 0
    assert len(calls) == spent
    kept = work / "memory-snapshot" / corpus.name / "memory" / "ledger-cache.md"
    assert "changed" not in kept.read_text(encoding="utf-8")


def test_arm_c_alone_skips_the_combined_arm_until_arm_a_has_run(corpus, memory, tmp_path, monkeypatch):
    from mnemo.core import llm

    monkeypatch.setattr(llm, "call", _stub([]))
    work = tmp_path / "work"

    assert day.main(["--send", "--arm", "c", "--corpus", str(corpus), "--work", str(work),
                     "--install-after", "3", "--prompts", "4"]) == 0

    progress = json.loads((work / "progress.json").read_text(encoding="utf-8"))
    assert progress["c"]["units"] and progress["ac"] == {"skipped": "arm (a) has not run; --arm a first"}
    assert "a" not in progress and "b" not in progress


def test_arm_c_refuses_a_plan_over_its_own_budget(corpus, memory, tmp_path, monkeypatch):
    from mnemo.core import llm

    monkeypatch.setattr(llm, "call", lambda *a, **k: pytest.fail("called a model"))

    with pytest.raises(SystemExit, match="over the 1 budget"):
        day.main(["--send", "--arm", "c", "--corpus", str(corpus), "--work", str(tmp_path / "w"),
                  "--install-after", "3", "--budget-c", "1"])


def test_the_dry_run_plans_arm_c_from_the_snapshot(corpus, memory, tmp_path, monkeypatch, capsys):
    from mnemo.core import llm

    monkeypatch.setattr(llm, "call", lambda *a, **k: pytest.fail("the dry run called a model"))

    assert day.main(["--dry-run", "--corpus", str(corpus), "--work", str(tmp_path / "w"),
                     "--install-after", "3"]) == 0

    out = capsys.readouterr().out
    assert "2 of today's 3 file(s) present" in out and "'created after': 1" in out
    assert not (tmp_path / "w").exists()
