"""``tools/measure_rule_lift.py`` over synthetic transcripts and synthetic pairs.

No model is called: the provider is a function the test hands in. What is
pinned is what the measurement has to be right about — that the two arms
differ by the hook's own injection and nothing else, that the judge cannot
tell the arms apart, that a pair is read in the context it was typed in, the
paired arithmetic and the decision rule, and that a run interrupted after any
call resumes without asking again.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from mnemo.core import llm

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_rule_lift.py"
_spec = importlib.util.spec_from_file_location("measure_rule_lift", _TOOL)
mrl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mrl)


def _uid(session_id, ts, text):
    return "u-" + text[:12]


def _line(kind, content, *, ts="2026-09-20T10:00:00Z", **extra):
    entry = {"type": kind, "timestamp": ts, "message": {"role": kind, "content": content}}
    entry.update(extra)
    return json.dumps(entry)


def _text(t):
    return [{"type": "text", "text": t}]


# --- reading a prompt in its context -------------------------------------------

def test_the_previous_turn_is_the_assistant_text_since_the_prompt_before():
    lines = [
        _line("user", "first question"),
        _line("assistant", _text("old reply")),
        _line("user", "sim, pode rodar antes"),
        _line("assistant", _text("I will run the migration.")),
        _line("assistant", [{"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]),
        _line("user", [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]),
        _line("assistant", _text("Done. Merge now?")),
        _line("assistant", _text("side chatter"), isSidechain=True),
        _line("user", "sim, pode mergear"),
    ]

    got = mrl.prompt_in_context(lines, "s", "u-sim, pode me", _uid)

    assert got == ("sim, pode mergear", "I will run the migration.\n\nDone. Merge now?")


def test_a_session_opener_has_no_previous_turn_and_a_missing_prompt_is_none():
    lines = [_line("user", "Work on issue #1")]

    assert mrl.prompt_in_context(lines, "s", "u-Work on issu", _uid) == ("Work on issue #1", "")
    assert mrl.prompt_in_context(lines, "s", "u-nowhere", _uid) is None


def test_a_bang_command_output_reads_the_reply_before_its_input():
    """``! cmd`` writes a ``<bash-input>`` prompt and then its output, with no
    reply between: the turn the developer was answering is the one before."""
    lines = [
        _line("user", "deploy it"),
        _line("assistant", _text("Run the dump yourself: pg_dump ...")),
        _line("user", "<bash-input>pg_dump x</bash-input>"),
        _line("user", "<bash-stdout>dumped</bash-stdout>"),
    ]

    got = mrl.prompt_in_context(lines, "s", "u-<bash-stdout", _uid)

    assert got is not None and got[1] == "Run the dump yourself: pg_dump ..."


def test_the_previous_turn_keeps_its_tail():
    assert mrl.tail("x" * 10, 4) == "…xxxx"
    assert mrl.tail("short", 40) == "short"


# --- the population ----------------------------------------------------------

def _index(*slugs):
    return {"docs": {s: {"preview": "Preview of %s.\nSecond line." % s} for s in slugs}}


def test_only_inject_labels_make_pairs_and_a_lost_transcript_stays_flagged(tmp_path):
    labels = {"u1": {"r-a": 2, "r-b": 1, "r-c": 0}, "u2": {"r-a": 2, "r-d": 2}}
    assert mrl.on_point_pairs(labels) == [("u1", "r-a"), ("u2", "r-a"), ("u2", "r-d")]

    tr = tmp_path / "s1.jsonl"
    tr.write_text("\n".join([_line("assistant", _text("before")),
                             _line("user", "the full prompt, longer than stored")]),
                  encoding="utf-8")
    units = {
        "u1": {"session_id": "s1", "project": "p", "prompt": "the full",
               "candidates": [{"slug": s, "text": "rule " + s} for s in ("r-a", "r-b", "r-c")]},
        "u2": {"session_id": "gone", "project": "p", "prompt": "stored text",
               "candidates": [{"slug": s, "text": "rule " + s} for s in ("r-a", "r-d")]},
    }
    uid_of = lambda sid, ts, text: "u1" if sid == "s1" else "?"  # noqa: E731

    pairs = mrl.build_pairs(units, labels, {"s1": tr}, _index("r-a", "r-d"), uid_of)

    assert [p["id"] for p in pairs] == ["u1:r-a", "u2:r-a", "u2:r-d"]
    first, lost = pairs[0], pairs[1]
    assert first["prompt"] == "the full prompt, longer than stored"
    assert first["previous"] == "before" and first["context"] is True
    assert first["rule"] == "rule r-a"
    assert lost["prompt"] == "stored text" and lost["previous"] == "" and lost["context"] is False


# --- the arms ------------------------------------------------------------------

def test_the_injection_is_the_hooks_own_output(capsys):
    from mnemo.hooks import user_prompt_submit as ups

    index = _index("r-a")
    ups._emit_reflex_context(index, ["r-a"])
    emitted = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]

    assert mrl.injection(index, "r-a") == emitted
    assert emitted.startswith("mnemo reflex context:\n• [[r-a]]: Preview of r-a. Second line.")


def _pair(pid="u1:r-a", slug="r-a", previous="Merge now?"):
    return {"id": pid, "uid": pid.split(":")[0], "slug": slug, "project": "p",
            "prompt": "sim", "previous": previous, "context": True,
            "rule": "Merge with --admin.",
            "injection": "mnemo reflex context:\n• [[%s]]: Merge with --admin." % slug}


def test_the_arms_differ_by_the_reminder_and_nothing_else():
    p = _pair()
    a, b = mrl.arm_prompt(p, "A"), mrl.arm_prompt(p, "B")

    assert b.startswith(a)
    assert b[len(a):] == ("\n\n<system-reminder>\nUserPromptSubmit hook additional context: "
                          + p["injection"] + "\n</system-reminder>")
    assert "Merge now?" in a and "sim" in a and "--admin" not in a
    assert "<assistant>" not in mrl.arm_prompt(_pair(previous=""), "A")


def test_calls_alternate_which_arm_goes_first_and_resume_skips_answered():
    pairs = [_pair("u%d:r" % i, "r") for i in range(20)]
    firsts = {mrl.arm_order(p["id"])[0] for p in pairs}
    assert firsts == {"A", "B"}

    answers = {"u0:r": {"A": [{"text": "x"}], "B": [{"text": "y"}]}, "u1:r": {"A": [{"text": "x"}]}}
    todo = mrl.pending_calls(pairs[:2], answers, samples=1)
    assert todo == [(pairs[1], "B")]
    # A second sample is owed by every arm, after the first sample's gaps.
    assert len(mrl.pending_calls(pairs[:2], answers, samples=2)) == 5


# --- the judge -------------------------------------------------------------------

def test_the_judge_cannot_tell_the_arms_apart():
    """Nothing an item carries names its arm, and a B answer quoting the
    injection back is masked the same way an A answer would be."""
    p = _pair()
    answers = {p["id"]: {
        "A": [{"text": "Merging with --admin."}],
        "B": [{"text": "Per [[r-a]] (read_mnemo_rule r-a), merging with --admin. mnemo reflex context"}],
    }}

    items = mrl.judge_items([p], answers, {})
    prompt = mrl.judge_prompt(items)

    assert {i["id"] for i in items} == {"u1:r-a|A|0", "u1:r-a|B|0"}
    assert set(items[0]) == {"id", "rule", "answer"}
    for banned in ("|A|", "|B|", "r-a", "read_mnemo_rule", "reflex context", "system-reminder"):
        assert banned not in prompt
    assert mrl.mask("see [[x]] and r-a", "r-a") == ("see [...] and [...]", True)
    assert mrl.mask("plain", "r-a") == ("plain", False)


def test_items_are_shuffled_the_same_way_every_run_and_judged_ones_skipped():
    pairs = [_pair("u%d:r" % i, "r") for i in range(8)]
    answers = {p["id"]: {a: [{"text": a}] for a in mrl.ARMS} for p in pairs}

    one = [i["id"] for i in mrl.judge_items(pairs, answers, {})]
    two = [i["id"] for i in mrl.judge_items(pairs, answers, {})]

    assert one == two and one != sorted(one)
    assert len(mrl.judge_items(pairs, answers, {one[0]: "yes"})) == 15


def test_verdicts_parse_tolerantly_and_illegible_items_stay_pending():
    batch = [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}]
    text = '```json\n{"1": "Yes", "2": "N/A", "3": "maybe"}\n```'

    assert mrl.parse_verdicts(text, batch) == {"a": "yes", "b": "na"}
    assert mrl.parse_verdicts("no json here", batch) == {}


# --- the numbers ------------------------------------------------------------------

def _answers(pids, samples=1):
    return {pid: {a: [{"text": ""}] * samples for a in mrl.ARMS} for pid in pids}


def _v(pid, a, b):
    return {mrl.answer_id(pid, "A", 0): a, mrl.answer_id(pid, "B", 0): b}


def test_lift_is_paired_and_both_na_pairs_are_left_out():
    pairs = [_pair("p%d:r" % i, "r%d" % (i % 2)) for i in range(5)]
    verdicts = {}
    verdicts.update(_v("p0:r", "no", "yes"))   # gained
    verdicts.update(_v("p1:r", "no", "yes"))   # gained
    verdicts.update(_v("p2:r", "yes", "yes"))  # redundant
    verdicts.update(_v("p3:r", "na", "no"))    # counts: one arm judged it applicable
    verdicts.update(_v("p4:r", "na", "na"))    # left out

    rows = mrl.pair_rows(pairs, _answers([p["id"] for p in pairs]), verdicts)
    stats = mrl.lift(rows, n_boot=2000)

    assert stats["n"] == 4 and stats["excluded_na"] == 1
    assert stats["lift"] == pytest.approx(0.5)
    assert stats["follow_a"] == pytest.approx(0.25) and stats["follow_b"] == pytest.approx(0.75)
    assert stats["redundant"] == pytest.approx(0.25)
    assert (stats["gained"], stats["lost"]) == (2, 0)
    lo, hi = stats["ci"]
    assert 0.0 <= lo <= 0.5 <= hi <= 1.0


def test_a_pair_not_yet_fully_judged_is_not_counted():
    pairs = [_pair("p0:r", "r")]
    rows = mrl.pair_rows(pairs, _answers(["p0:r"]), {mrl.answer_id("p0:r", "A", 0): "yes"})
    assert rows == [] and mrl.lift(rows)["n"] == 0


def test_samples_average_within_an_arm():
    pairs = [_pair("p0:r", "r")]
    verdicts = {mrl.answer_id("p0:r", "A", 0): "no", mrl.answer_id("p0:r", "A", 1): "yes",
                mrl.answer_id("p0:r", "B", 0): "yes", mrl.answer_id("p0:r", "B", 1): "yes"}
    (row,) = mrl.pair_rows(pairs, _answers(["p0:r"], samples=2), verdicts)
    assert (row["A"], row["B"]) == (0.5, 1.0)


def test_the_bootstrap_is_seeded():
    rows = [{"id": str(i), "slug": "r", "A": float(i % 2), "B": float(i % 3 > 0), "both_na": False}
            for i in range(30)]
    assert mrl.lift(rows, n_boot=500)["ci"] == mrl.lift(rows, n_boot=500)["ci"]


@pytest.mark.parametrize("lift_, ci, branch", [
    (0.30, (0.15, 0.45), "rules carry"),
    (0.05, (-0.05, 0.15), "stop tuning recall/reflex"),
    (0.06, (0.01, 0.11), "lift is under 10 pp"),
    (-0.2, (-0.3, -0.1), "lift is negative"),
])
def test_the_decision_rule_is_the_pre_registered_one(lift_, ci, branch):
    assert branch in mrl.decision({"n": 50, "lift": lift_, "ci": ci})


def test_per_rule_lift_lists_the_rules_to_retire_first():
    rows = [
        {"id": "1", "slug": "keeps", "A": 0.0, "B": 1.0, "both_na": False},
        {"id": "2", "slug": "idle", "A": 1.0, "B": 1.0, "both_na": False},
        {"id": "3", "slug": "idle", "A": 0.0, "B": 0.0, "both_na": False},
        {"id": "4", "slug": "unjudgeable", "A": 0.0, "B": 0.0, "both_na": True},
    ]
    assert mrl.per_rule(rows) == [("idle", 2, 0.0), ("keeps", 1, 1.0)]


def test_the_dry_run_counts_two_calls_a_pair_and_a_judge_call_per_ten_answers():
    pairs = [_pair("u%d:r%d" % (i // 2, i), "r%d" % i) for i in range(7)]
    e = mrl.estimate(pairs, samples=1)
    assert (e["pairs"], e["prompts"], e["rules"]) == (7, 4, 7)
    assert (e["arm_calls"], e["judge_calls"]) == (14, 2)
    assert mrl.estimate(pairs, samples=2)["arm_calls"] == 28
    assert e["usd"] > 0


# --- the run -------------------------------------------------------------------------

class _Stop(Exception):
    pass


def _fake_provider(seen, stop_after=None):
    """Arm B follows the rule, arm A does not; the judge reads the answer only."""
    def call(prompt, *, system, model, timeout):
        seen.append((system, prompt))
        if stop_after is not None and len(seen) > stop_after:
            raise _Stop()
        if system == mrl.JUDGE_SYSTEM:
            n = prompt.count("## Item ")
            out = {str(i): ("yes" if "--admin" in chunk.split("ANSWER:")[1] else "no")
                   for i, chunk in enumerate(prompt.split("## Item ")[1:], 1)}
            assert len(out) == n
            text = json.dumps(out)
        else:
            text = "I will merge with --admin." if "<system-reminder>" in prompt else "I will merge."
        return llm.LLMResponse(text=text, total_cost_usd=0.01, input_tokens=10,
                               output_tokens=5, api_key_source=None, raw={})
    return call


def test_a_run_interrupted_after_any_call_resumes_without_asking_again(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    out = vault / ".mnemo" / mrl.OUT_DIR
    out.mkdir(parents=True)
    pairs = [_pair("u%d:r" % i, "r") for i in range(3)]
    for i, p in enumerate(pairs):
        p["prompt"] = "prompt %d" % i
    (out / mrl.PAIRS_NAME).write_text(json.dumps(pairs), encoding="utf-8")

    from mnemo.core import config, paths
    monkeypatch.setattr(config, "load_config",
                        lambda *a, **k: {"extraction": {"subprocessTimeout": 5}})
    monkeypatch.setattr(paths, "vault_root", lambda cfg: vault)
    cwd = Path.cwd()

    seen = []
    monkeypatch.setattr(llm, "resolve", lambda cfg: _fake_provider(seen, stop_after=4))
    with pytest.raises(_Stop):
        mrl.main(["--send"])
    assert Path.cwd() == cwd  # the scratch directory is left, even on failure
    assert all(s == mrl.ARM_SYSTEM for s, _ in seen)

    answered = {p for _, p in seen[:4]}  # the fifth call raised: never answered
    seen.clear()
    monkeypatch.setattr(llm, "resolve", lambda cfg: _fake_provider(seen))
    assert mrl.main(["--send"]) == 0
    report = capsys.readouterr().out

    arm_calls = [p for s, p in seen if s == mrl.ARM_SYSTEM]
    assert len(arm_calls) == 2 and not set(arm_calls) & answered
    judge_calls = [p for s, p in seen if s == mrl.JUDGE_SYSTEM]
    assert len(judge_calls) == 1 and judge_calls[0].count("## Item ") == 6
    assert "lift B - A: +100.0 pp" in report
    assert "rules carry" in report
    assert "not money spent" in report

    # Nothing left to do: a third run asks nothing.
    seen.clear()
    assert mrl.main(["--send"]) == 0
    assert seen == []


def test_pairs_are_never_rebuilt_under_existing_answers(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    out = vault / ".mnemo" / mrl.OUT_DIR
    out.mkdir(parents=True)
    (out / mrl.ANSWERS_NAME).write_text(json.dumps({"m@x": {"u:r": {"A": [{"text": "t"}]}}}),
                                        encoding="utf-8")
    from mnemo.core import config, paths
    monkeypatch.setattr(config, "load_config", lambda *a, **k: {"extraction": {}})
    monkeypatch.setattr(paths, "vault_root", lambda cfg: vault)

    with pytest.raises(SystemExit, match="refusing to rebuild"):
        mrl.main(["--dry-run"])
