"""``tools/measure_repeated_corrections.py`` over synthetic transcripts, vaults,
repos and auto-memory shaped like the real ones (#519)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_repeated_corrections as tool  # noqa: E402

from mnemo.core import llm  # noqa: E402
from mnemo.core.friction import ledger  # noqa: E402
from mnemo.core.mcp import access_log  # noqa: E402
from mnemo.core.reflex import index as reflex_index  # noqa: E402

CWD = "/Users/you/github/app"


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _user(text, ts, *, cwd=CWD, uuid=""):
    return {"type": "user", "timestamp": ts, "cwd": cwd, "entrypoint": "cli", "uuid": uuid,
            "message": {"role": "user", "content": text}}


def _agent(text, ts):
    return {"type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _briefing(*quotes):
    items = "".join('- "%s" → rule for %s\n' % (q, q) for q in quotes)
    return "---\ntype: briefing\ndate: 2026-09-20\n---\n\n## TL;DR\nx\n\n## Corrections\n" + items


def _write_rule(vault, slug, body, *, sources, extra=""):
    path = vault / "shared" / "feedback" / (slug + ".md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nname: %s\ndescription: d\nstability: stable\nsources:\n%s%s---\n%s\n"
        % (slug, "".join("  - %s\n" % s for s in sources), extra, body),
        encoding="utf-8")
    return path


def _src(sid, agent="app"):
    return "bots/%s/briefings/sessions/%s.md" % (agent, sid)


@pytest.fixture
def telemetry_on(monkeypatch):
    monkeypatch.setattr(access_log, "_load_telemetry_config", lambda: (True, 1_048_576))


# --- items ------------------------------------------------------------------------------

def test_briefing_items_are_verified_located_and_timed():
    ev = [_agent("I will use npm install.", "2026-09-21T09:59:00Z"),
          _user("never use npm in this repo, always yarn", "2026-09-21T10:00:00Z"),
          _user("<bash-input>gh pr merge 1 --admin</bash-input>", "2026-09-21T10:01:00Z")]
    text = _briefing("never use npm in this repo", "gh pr merge 1 --admin", "made up by the model")
    got = tool.session_items(ev, "sess-a", project="app", briefing_text=text, rows=[])
    assert len(got) == 1
    it = got[0]
    assert (it["path"], it["turn_index"], it["project"], it["cwd"]) == ("briefing", 0, "app", CWD)
    assert it["ts"] == _ts("2026-09-21T10:00:00Z") and it["week"] == "2026-W39"
    assert it["answered"] == "I will use npm install."
    assert it["id"] == tool.item_id("sess-a", 0, "never use npm in this repo")


def test_capture_rows_win_over_the_briefing(tmp_path, telemetry_on):
    ev = [_user("ok go ahead", "2026-09-21T10:00:00Z"),
          _user("stop adding emojis to commit messages", "2026-09-21T10:02:00Z")]
    rec = ledger.FrictionRecord(session_id="s", project="app", quote="stop adding emojis",
                                rule_text="No emojis", capture="corrections_only", turn_index=1)
    ledger.record(tmp_path, rec)
    ledger.record(tmp_path / "b", rec)  # same row through a second file counts once
    rows = tool.capture_rows([ledger.ledger_path(tmp_path), ledger.ledger_path(tmp_path / "b")])
    assert len(rows["s"]) == 1
    got = tool.session_items(ev, "s", project="app", briefing_text=_briefing("ok go ahead"),
                             rows=rows["s"])
    assert [(i["path"], i["turn_index"], i["rule"]) for i in got] == [("corrections_only", 1, "No emojis")]


# --- dating a rule ----------------------------------------------------------------------

def test_first_learned_uses_the_row_when_there_is_one_else_the_earliest_foreign_source():
    with_row = {"stamped": 500.0, "has_row": True, "sources": [("old", 100.0)]}
    assert tool.first_learned(with_row, "me") == 500.0
    no_row = {"stamped": 900.0, "has_row": False, "sources": [("me", 50.0), ("old", 300.0)]}
    # the correction's own session never dates the rule it may have produced
    assert tool.first_learned(no_row, "me") == 300.0
    assert tool.first_learned(no_row, "someone") == 50.0
    assert tool.first_learned({"stamped": None, "has_row": False, "sources": []}, "me") is None


def test_rule_dates_read_learned_rows_frontmatter_sources_and_imports(tmp_vault):
    (tmp_vault / ".mnemo").mkdir()
    (tmp_vault / ".mnemo" / "learned.jsonl").write_text(
        json.dumps({"seq": 1, "ts": "2026-09-10T12:00:00", "slug": "row-rule"}) + "\n"
        + json.dumps({"seq": 2, "ts": "2026-09-12T12:00:00", "slug": "row-rule"}) + "\n",
        encoding="utf-8")
    _write_rule(tmp_vault, "row-rule", "use yarn", sources=[_src("s1")],
                extra="extracted_at: 2026-09-20T00:00:00\n")
    _write_rule(tmp_vault, "imported", "a note", sources=["bots/app/memory/note.md"],
                extra="promoted_at: 2026-09-05T00:00:00\n")
    learned = tool.learned_first(tmp_vault)
    assert learned["row-rule"] == datetime(2026, 9, 10, 12).timestamp()
    dates = tool.rule_dates(tmp_vault, learned=learned, session_end={"s1": 42.0})
    assert dates["row-rule"]["has_row"] and dates["row-rule"]["stamped"] == learned["row-rule"]
    assert dates["row-rule"]["sources"] == [("s1", 42.0)]
    assert not dates["row-rule"]["imported"]
    assert dates["imported"]["imported"]
    assert dates["imported"]["stamped"] == datetime(2026, 9, 5).timestamp()


def test_transcript_end_is_its_last_timestamp(tmp_path):
    p = tmp_path / "proj" / "abc.jsonl"
    p.parent.mkdir()
    p.write_text("\n".join(json.dumps(e) for e in [
        _user("a", "2026-09-21T10:00:00Z"), _agent("b", "2026-09-21T11:30:00.500Z")]) + "\n",
        encoding="utf-8")
    assert tool.transcript_end_times(tmp_path) == {"abc": _ts("2026-09-21T11:30:00.500Z")}


# --- the as-of pool ---------------------------------------------------------------------

def test_as_of_candidates_keep_only_rules_that_existed_at_the_turn(tmp_vault):
    _write_rule(tmp_vault, "yarn-old", "never use npm, always yarn install", sources=[_src("old")])
    _write_rule(tmp_vault, "yarn-late", "never use npm, yarn only please", sources=[_src("late")])
    _write_rule(tmp_vault, "yarn-own", "npm is banned, yarn", sources=[_src("me")])
    _write_rule(tmp_vault, "yarn-verified", "npm never; yarn", sources=[_src("old")])
    ends = {"old": _ts("2026-09-01T10:00:00Z"), "late": _ts("2026-09-25T10:00:00Z"),
            "me": _ts("2026-09-21T12:00:00Z")}
    dates = tool.rule_dates(tmp_vault, learned={}, session_end=ends)
    idx = reflex_index.build_index(tmp_vault)
    item = {"quote": "never use npm", "rule": "always yarn", "project": "app",
            "session_id": "me", "ts": _ts("2026-09-21T10:00:00Z")}
    got = [s for s, _ in tool.as_of_candidates(idx, item, dates, {"yarn-verified"})]
    assert set(got) == {"yarn-old", "yarn-verified"}
    # another project's pool is not this correction's
    assert tool.as_of_candidates(idx, dict(item, project="other"), dates, set()) == []


def test_verified_rules_are_added_past_the_broad_cut(tmp_vault, monkeypatch):
    monkeypatch.setattr(tool, "BROAD_CANDIDATES", 2)
    for i in range(4):
        _write_rule(tmp_vault, "yarn-%d" % i, "never use npm " * (4 - i), sources=[_src("old")])
    dates = tool.rule_dates(tmp_vault, learned={}, session_end={"old": 1.0})
    idx = reflex_index.build_index(tmp_vault)
    item = {"quote": "never use npm", "rule": "", "project": "app", "session_id": "me", "ts": 10.0}
    got = [s for s, _ in tool.as_of_candidates(idx, item, dates, {"yarn-3"})]
    assert len(got) == 3 and got[-1] == "yarn-3"


# --- native memory ----------------------------------------------------------------------

def _git(repo, *args, when=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@e")
    if when:
        env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    subprocess.run(["git", "-C", str(repo)] + list(args), check=True, env=env,
                   capture_output=True)


def test_claude_md_is_read_at_the_last_commit_before_the_turn(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "CLAUDE.md").write_text("use yarn\n", encoding="utf-8")
    _git(repo, "add", "CLAUDE.md")
    _git(repo, "commit", "-qm", "a", when="2026-09-10T10:00:00+00:00")
    (repo / "CLAUDE.md").write_text("use yarn\nno emojis\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "b", when="2026-09-20T10:00:00+00:00")
    home = tmp_path / "claude-home"
    home.mkdir()
    assert tool.claude_md_as_of(repo, _ts("2026-09-05T00:00:00Z"), home) == []
    assert tool.claude_md_as_of(repo, _ts("2026-09-15T00:00:00Z"), home) == [
        ("CLAUDE.md (project)", "use yarn\n")]
    assert tool.claude_md_as_of(repo, _ts("2026-09-25T00:00:00Z"), home)[0][1].endswith("no emojis\n")


def test_repo_root_of_a_deleted_worktree_is_its_longest_named_sibling(tmp_path):
    for name in ("app", "app-desktop"):
        (tmp_path / name).mkdir()
    assert tool.repo_root_for(str(tmp_path / "app-wt-519")) == tmp_path / "app"
    assert tool.repo_root_for(str(tmp_path / "app-desktop-wt-3")) == tmp_path / "app-desktop"
    assert tool.repo_root_for(str(tmp_path / "zzz")) is None
    assert tool.repo_root_for("") is None


def test_memory_index_lines_and_notes_are_dated_by_the_notes(tmp_path, monkeypatch):
    home = tmp_path / "claude-home"
    mem = home / "projects" / tool._encode(str(tmp_path / "app")) / "memory"
    mem.mkdir(parents=True)
    (mem / "old.md").write_text("never use npm here, yarn only", encoding="utf-8")
    (mem / "new.md").write_text("never use npm either", encoding="utf-8")
    (mem / "MEMORY.md").write_text(
        "# Memory Index\n- [Old](old.md) — yarn\n- [New](new.md) — npm\n", encoding="utf-8")
    born = {"old.md": 100.0, "new.md": 300.0, "MEMORY.md": 900.0}  # the index is rewritten in place
    monkeypatch.setattr(tool, "_born", lambda p: born.get(Path(p).name))
    assert tool.memory_dir_for(str(tmp_path / "app"), None, home) == mem
    index, notes = tool.memory_as_of(mem, 200.0, "never use npm")
    assert index == "# Memory Index\n- [Old](old.md) — yarn"
    assert [n for n, _ in notes] == ["old"]
    assert tool.memory_as_of(mem, 50.0, "npm") == ("", [])


# --- the raters' answers ----------------------------------------------------------------

def _unit():
    item = {"id": "i1", "answered": "a", "turn": "t", "rule": "r", "quote": "q"}
    unit = tool.judge_unit(item, [("yarn-old", "Yarn", "use yarn"), ("mem-import", "M", "m")],
                           [("CLAUDE.md (project)", "no emojis")], "- [x](x.md)", [("x", "body")])
    return item, unit


def test_judge_unit_numbers_rules_instruction_files_and_memory():
    item, unit = _unit()
    assert {k: v["kind"] for k, v in unit["notes"].items()} == {
        "R1": "rule", "R2": "rule", "C1": "claude_md", "M1": "memory", "M2": "memory"}
    prompt = tool.judge_prompt(item, unit)
    assert "### R1 — Yarn" in prompt and "### C1" in prompt and "### M2 — x" in prompt


def test_parse_verdict_drops_unknown_notes_and_known_without_a_note():
    _, unit = _unit()
    assert tool.parse_verdict('{"known": true, "notes": ["R1", "R9"], "why": "w"}', unit) == {
        "known": True, "notes": ["R1"], "why": "w"}
    assert tool.parse_verdict('{"known": true, "notes": ["R9"]}', unit)["known"] is False
    assert tool.parse_verdict('{"known": false, "notes": ["R1"]}', unit)["notes"] == []
    assert tool.parse_verdict("no json here", unit) is None


def test_parse_labels_keeps_only_this_batchs_ids_and_booleans():
    batch = [{"id": "a"}, {"id": "b"}]
    text = '```json\n{"labels": [{"id": "a", "correction": true}, {"id": "b", "correction": "yes"},' \
           ' {"id": "z", "correction": false}]}\n```'
    assert tool.parse_labels(text, batch) == {"a": True}


def test_readings_split_strict_broad_native_and_own_session():
    item, unit = _unit()
    item["session_id"] = "me"
    dates = {"yarn-old": {"sources": [("me", 1.0), ("old", 0.5)], "imported": False},
             "mem-import": {"sources": [], "imported": True}}
    r = tool.readings({"known": True, "notes": ["R1"]}, unit, item, dates, {"yarn-old"})
    assert r == {"strict": True, "broad": True, "broad_clean": False, "native": False,
                 "vault_only": True}
    r = tool.readings({"known": True, "notes": ["R2"]}, unit, item, dates, set())
    assert (r["broad"], r["native"], r["vault_only"], r["broad_clean"]) == (True, True, False, True)
    r = tool.readings({"known": True, "notes": ["C1"]}, unit, item, dates, set())
    assert (r["broad"], r["native"]) == (False, True)


# --- the report -------------------------------------------------------------------------

def test_kappa_and_wilson():
    assert tool.kappa([True, False, True, False], [True, False, True, False]) == 1.0
    assert tool.kappa([True, True, False, False], [True, False, True, False]) == 0.0
    assert tool.kappa([], []) is None
    lo, hi = tool.wilson(0, 10)
    assert lo == 0.0 and 0.27 < hi < 0.28
    assert tool.wilson(0, 0) == (None, None)


def test_report_primary_reading_is_both_raters_agree():
    items = [{"id": i, "path": p, "week": "2026-W39", "ts": float(n)} for n, (i, p) in
             enumerate([("a", "briefing"), ("b", "briefing"), ("c", "corrections_only"),
                        ("d", "corrections_only")])]
    labels = {"m1": {"a": True, "b": True, "c": True, "d": False},
              "m2": {"a": True, "b": False, "c": True}}  # d not labelled by m2 yet
    yes = dict(strict=True, broad=True, broad_clean=True, native=False, vault_only=True)
    no = dict(strict=False, broad=False, broad_clean=False, native=False, vault_only=False)
    judged = {"m1": {"a": yes, "c": yes}, "m2": {"a": yes, "c": dict(no, native=True)}}
    data = tool.report(items, labels, judged)
    assert data["labelled"] == 3 and data["real"] == 2 and data["judged"] == 2
    p = data["precision"]
    assert (p["both"]["briefing"]["k"], p["both"]["briefing"]["n"]) == (1, 2)
    assert (p["m1"]["briefing"]["k"], p["m2"]["corrections_only"]["k"]) == (2, 1)
    r = data["rates"]
    assert (r["both"]["broad"]["k"], r["m1"]["broad"]["k"], r["m2"]["native"]["k"]) == (1, 2, 1)
    assert data["weeks"] == [dict(week="2026-W39", n=2, strict=1, broad=1, broad_clean=1,
                                  native=0, vault_only=1)]
    assert set(data["agreement"]) == {"label_kappa", "strict_kappa", "broad_kappa"}
    text = "\n".join(tool.report_lines(data))
    assert "both" in text and "m2" in text and "kappa" in text


def test_run_calls_paces_logs_and_survives_a_failed_call(tmp_path):
    slept, got = [], []

    def provider(prompt, *, system, model, timeout):
        if prompt == "boom":
            raise llm.LLMSubprocessError("x")
        return llm.LLMResponse(text=prompt.upper(), total_cost_usd=0.5, input_tokens=10,
                               output_tokens=2, api_key_source=None, raw={})
    calls = [(p, "sys", got.append) for p in ("one", "boom", "two")]
    usd = tool.run_calls(provider, "m1", 5, calls, None, tmp_path / "calls.jsonl", 3.0, slept.append)
    assert usd == 1.0 and got == ["ONE", "TWO"] and slept == [3.0, 3.0]
    assert tool.spent_by_rater(tmp_path / "calls.jsonl", ["m1", "m2"]) == {
        "m1": {"usd": 1.0, "calls": 2}, "m2": {"usd": 0.0, "calls": 0}}


# --- end to end -------------------------------------------------------------------------

def _setup(tmp_path):
    projects, vault = tmp_path / "projects", tmp_path / "vault"
    proj = projects / "-Users-you-github-app"
    proj.mkdir(parents=True)
    (vault / "shared").mkdir(parents=True)
    old = [_user("set up the repo please", "2026-09-01T10:00:00Z")]
    now = [_agent("Running npm install now.", "2026-09-21T09:59:00Z"),
           _user("never use npm in this repo, always yarn", "2026-09-21T10:00:00Z"),
           _user("sim, pode mergear o PR agora", "2026-09-21T10:05:00Z")]
    for sid, events in (("old00001-x", old), ("now00002-x", now)):
        (proj / (sid + ".jsonl")).write_text("\n".join(json.dumps(e) for e in events) + "\n",
                                             encoding="utf-8")
    d = vault / "bots" / "app" / "briefings" / "sessions"
    d.mkdir(parents=True)
    (d / "now00002-x.md").write_text(
        _briefing("never use npm in this repo", "sim, pode mergear o PR agora"), encoding="utf-8")
    _write_rule(vault, "yarn-not-npm", "Never use npm in this repo; always yarn.",
                sources=[_src("old00001-x")])
    return projects, vault


def test_main_dry_run_calls_nothing_and_send_labels_then_judges(tmp_path, capsys, monkeypatch):
    projects, vault = _setup(tmp_path)
    home = tmp_path / "claude-home"
    home.mkdir()
    base = ["--projects", str(projects), "--vault", str(vault), "--claude-home", str(home),
            "--out", str(tmp_path / "out"), "--rater", "m1", "--rater", "m2", "--pause", "0"]

    def no_model(cfg):
        raise AssertionError("a dry run must not resolve a provider")
    monkeypatch.setattr(llm, "resolve", no_model)
    assert tool.main(base + ["--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["items"] == 2 and "notional estimate" in captured.err

    asked = []

    def provider(prompt, *, system, model, timeout):
        asked.append((model, system == tool.LABEL_SYSTEM))
        if system == tool.LABEL_SYSTEM:
            ids = [line.split()[-1] for line in prompt.splitlines() if line.startswith("### item")]
            body = {"labels": [{"id": i, "correction": "npm" in prompt.split(i, 1)[1][:200]}
                               for i in ids]}
        else:
            assert "yarn-not-npm" not in prompt or "Never use npm" in prompt
            body = {"known": True, "notes": ["R1"], "why": "same rule"}
        return llm.LLMResponse(text=json.dumps(body), total_cost_usd=0.01, input_tokens=1,
                               output_tokens=1, api_key_source=None, raw={})
    monkeypatch.setattr(llm, "resolve", lambda cfg: provider)
    assert tool.main(base + ["--send", "--json", "--examples", "5"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["real"] == 1 and data["judged"] == 1
    assert data["rates"]["both"]["broad"]["k"] == 1
    assert data["precision"]["both"]["briefing"]["n"] == 2
    assert asked == [("m1", True), ("m2", True), ("m1", False), ("m2", False)]
    assert data["cost"]["m1"] == {"usd": 0.02, "calls": 2}

    # a rerun resumes from the cache: nothing is asked again
    asked.clear()
    assert tool.main(base + ["--send", "--json"]) == 0
    assert asked == [] and json.loads(capsys.readouterr().out)["judged"] == 1


def test_main_budget_refuses_before_any_call(tmp_path, capsys, monkeypatch):
    projects, vault = _setup(tmp_path)
    monkeypatch.setattr(llm, "resolve", lambda cfg: pytest.fail("no call over budget"))
    assert tool.main(["--projects", str(projects), "--vault", str(vault),
                      "--claude-home", str(tmp_path), "--out", str(tmp_path / "out"),
                      "--send", "--budget", "0"]) == 2
    assert "refusing" in capsys.readouterr().err
