"""``tools/measure_day_one_gate.py`` over the synthetic corpus of ``test_measure_day_one``.

No model and no TypeSafe: ``core.llm.call`` answers by system prompt and the
Jev client is a stub. What is pinned: the real ``reference_gate.judge_pages``
decides which project pages are held, a G/N page is gone from the
counterfactual vault and never injected, a pool #479 already judged is not
sent again, the budgets hold, and a rerun spends nothing.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

from tests.unit import test_measure_day_one as base
from tests.unit.test_measure_day_one import corpus, memory  # noqa: F401 — fixtures

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_day_one_gate.py"
_spec = importlib.util.spec_from_file_location("measure_day_one_gate", _TOOL)
gate = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = gate
_spec.loader.exec_module(gate)
day = gate.day


def _gate_stub(calls, *, junk=("webhook",)):
    """The helpers' stub, with the reference gate calling a page G when its name starts with ``junk``."""
    from mnemo.core import llm
    from mnemo.core.extract import reference_gate

    inner = base._stub(calls)

    def call(prompt, *, system=None, model="", timeout=60):
        if system != reference_gate.SYSTEM_PROMPT:
            return inner(prompt, system=system, model=model, timeout=timeout)
        calls.append(model)
        entries = re.findall(r"^\[(\d+)\] ([^.]+)\.", prompt, re.M)
        text = json.dumps({"verdicts": [{"i": int(i), "cat": "G" if name.startswith(junk) else "S"}
                                        for i, name in entries]})
        return llm.LLMResponse(text=text, total_cost_usd=0.01, input_tokens=10, output_tokens=10,
                               api_key_source="none", raw={})

    return call


@pytest.fixture
def judged_c(corpus, memory, tmp_path, monkeypatch):  # noqa: F811 — fixtures
    """Arm (c) run with two live project pages, then judged with the Jev stub (#479)."""
    from mnemo.core import llm
    from mnemo.core.mcp import rerank
    from mnemo.core.reflex import judge

    install = day.load_sessions(corpus)[3].start
    (memory / "webhook-status.md").write_text(
        base._mem("webhook-status", "project", "replaying payment webhooks: " + base.WORDS),
        encoding="utf-8")
    os.utime(str(memory / "webhook-status.md"), (install - 3600, install - 3600))
    calls, sent = [], []
    monkeypatch.setattr(llm, "call", _gate_stub(calls))
    monkeypatch.setattr(rerank, "typesafe_client", lambda key, **kw: base._stub_jev(sent))
    monkeypatch.setattr(judge, "resolve_key", lambda chosen: ("sk-test", "env"))
    work = tmp_path / "work"
    common = ["--corpus", str(corpus), "--work", str(work)]
    assert day.main(["--send", "--arm", "c", "--install-after", "3", "--prompts", "4"] + common) == 0
    assert day.main(["--judge", "--send", "--arm", "c"] + common) == 0
    assert len(list((work / "arm-c" / "vault" / "shared" / "project").glob("*.md"))) == 2
    return {"work": work, "calls": calls, "sent": sent, "args": common}


def test_the_gate_holds_g_pages_out_of_the_replay_and_a_rerun_spends_nothing(judged_c, capsys):
    work, calls, sent = judged_c["work"], judged_c["calls"], judged_c["sent"]
    calls_before, sent_before = len(calls), len(sent)

    assert gate.main(judged_c["args"] + ["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "2 live project page(s) in 1 chunk(s) of 10; 1 call(s) to claude-sonnet-5" in out
    assert (len(calls), len(sent)) == (calls_before, sent_before)
    assert not (work / gate.GATE_NAME).exists()

    assert gate.main(judged_c["args"] + ["--send"]) == 0
    state = json.loads((work / gate.GATE_NAME).read_text(encoding="utf-8"))
    assert state["gate_calls"] == 1 and calls[calls_before] == "claude-sonnet-5"
    (held,) = state["held"]
    assert "webhook" in held and sorted(state["verdicts"].values()) == ["G", "S"]
    # The counterfactual vault lacks the held page and nothing injects it.
    held_vault = work / gate.HELD_DIR / "vault"
    assert not list(held_vault.glob("shared/project/%s.md" % held))
    assert len(list(held_vault.glob("shared/project/*.md"))) == 1
    assert (work / "arm-c" / "vault" / "shared" / "project" / (held + ".md")).exists()
    for u in state["off_units"] + state["units"]:
        assert held not in [c["slug"] for c in u["pool"]]
    assert any(u["pool"] for u in state["units"])
    assert state["requests"] == len(sent) - sent_before <= 100
    assert state["gate_calls"] + state["label_calls"] <= 15
    # Every new pair is labelled, in the rater column #472/#479 share.
    data = gate.report(state, gate.load_source(work, Path(judged_c["args"][1])), json.loads(
        (work / "labels.json").read_text(encoding="utf-8"))[day.mrr.column(day.DEFAULT_RATER)])
    for name in ("held off", "held on"):
        assert data["runs"][name]["labelled"] == data["runs"][name]["pairs"]
    # The stub rater calls every pair on-point, so the held page's injections are the cost.
    assert data["cost_slugs"] == [held]
    out = capsys.readouterr().out
    assert "G 1, S 1" in out and "held on" in out and "the gate's cost): 1" in out

    spent = (len(calls), len(sent))
    assert gate.main(judged_c["args"] + ["--send"]) == 0
    assert (len(calls), len(sent)) == spent
    assert gate.main(judged_c["args"]) == 0
    assert "479 on" in capsys.readouterr().out


def test_the_replay_refuses_a_bound_over_the_jev_budget(judged_c):
    sent = len(judged_c["sent"])
    with pytest.raises(SystemExit, match="Jev request"):
        gate.main(judged_c["args"] + ["--send", "--jev-budget", "0"])
    assert len(judged_c["sent"]) == sent


def _page(slug, name=None):
    return gate.GatePage(slug=slug, name=name or slug, body="a body about " + slug)


def test_run_gate_asks_only_unanswered_chunks_and_stops_at_the_budget():
    from mnemo.core import llm

    pages = [_page("p%d" % i) for i in range(5)]
    asked = []

    def ask(prompt):
        asked.append(prompt)
        n = len(re.findall(r"^\[\d+\]", prompt, re.M))
        return json.dumps({"verdicts": [{"i": i, "cat": "N"} for i in range(1, n + 1)]})

    verdicts = {"p0": "S", "p1": "T"}
    assert gate.run_gate(pages, verdicts, ask, 2, calls_left=1) == 1
    assert verdicts == {"p0": "S", "p1": "T", "p2": "N", "p3": "N"} and len(asked) == 1
    assert gate.run_gate(pages, verdicts, ask, 2, calls_left=5) == 1
    assert gate.held_slugs(verdicts) == ["p2", "p3", "p4"]

    # A failed call is no answer, holds nothing, and is asked again next time.
    def broken(prompt):
        raise llm.LLMSubprocessError("down")

    verdicts = {}
    assert gate.run_gate(pages[:2], verdicts, broken, 10, calls_left=5) == 1
    assert verdicts == {"p0": "", "p1": ""} and gate.held_slugs(verdicts) == []
    assert gate.verdict_counts(verdicts) == {"none": 2}


def test_a_pool_479_judged_is_answered_from_its_scores_and_a_changed_one_is_sent(tmp_path):
    from mnemo.core.reflex import replay
    from mnemo.install import scaffold

    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    for slug in ("a", "b", "c"):
        base._live(vault, "feedback", slug)

    sent = []
    cache = {("s", 1.0): {"status": "ok", "asked": 2, "injected": 1, "ms": 5,
                          "scores": [["b", 0.7], ["a", 0.1]]}}
    jev = gate.CachedJev(base._stub_jev(sent), day.shipped_judge(), 10, cache)
    rows = {}
    stage = jev.stage(vault, None, rows)
    prompt = replay.Prompt(session_id="s", project="p", ts=1.0, text="t")

    assert stage(prompt, ["a", "b"]) == ["b"]
    assert sent == [] and jev.hits == 1 and jev.sent == 0 and rows[("s", 1.0)]["cached"]
    # A pool with a rule #479 never scored for this prompt goes to Jev.
    jev.stage(vault, None, rows)(prompt, ["a", "c"])
    assert jev.hits == 1 and jev.sent == 1

