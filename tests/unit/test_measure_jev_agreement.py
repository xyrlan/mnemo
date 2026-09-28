"""``tools/measure_jev_agreement.py`` over synthetic labels and a stub Jev (#529)."""
from __future__ import annotations

import io
import json
import random
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_jev_agreement as tool  # noqa: E402

R1, R2 = tool.RATERS


# --- synthetic items ---------------------------------------------------------------------

def _items(n=120, groups=60, agree=0.85, seed=1, question="correction"):
    """Binary items two raters labelled from a hidden truth, each flipping it
    with probability ``1 - agree``; ``truth`` rides along for the stub."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        truth = rng.random() < 0.4
        labels = {r: truth if rng.random() < agree else not truth for r in (R1, R2)}
        g = "s%d" % (i % groups)
        out.append({"id": "i%03d" % i, "group": g, "state": {"user": "turn %d" % i},
                    "state_key": "k%d" % i, "subject": "rule %d" % i, "labels": labels, "truth": truth})
    return out


class Stub:
    """Answers every noul/score/choice question from the item's truth, and
    records which items it was asked about and whether a lock existed then."""

    def __init__(self, items, out_dir, noise=0.0, seed=2, answer=None):
        self.by_text = {}
        for it in items:
            subj = it["subject"] if isinstance(it["subject"], str) else it["subject"][0]
            self.by_text[subj] = it
        self.out_dir = out_dir
        self.rng = random.Random(seed)
        self.noise = noise
        self.asked = []  # (item id, lock exists?)
        self.answer = answer

    def _item(self, question):
        ins = question["instructions"]
        for text, it in self.by_text.items():
            if ins.endswith(text):
                return it
        for text, it in self.by_text.items():
            if text in ins:
                return it
        raise AssertionError("unknown question")

    def __call__(self, state, questions):
        locked = (self.out_dir / "lock.json").exists()
        answers = {}
        for qid, q in questions.items():
            it = self._item(q)
            self.asked.append((it["id"], locked))
            truth = it["truth"] if self.rng.random() >= self.noise else not it["truth"]
            p = 0.9 if truth else 0.1
            if self.answer is not None:
                answers[qid] = self.answer(q, it, p)
            elif q["type"] == "noul":
                answers[qid] = {"type": "noul", "noul": p}
            elif q["type"] == "score":
                answers[qid] = {"type": "score", "score": 2 * p}
            else:
                answers[qid] = {"type": "choice", "probabilities": {"1": p, "2": 1 - p, "tie": 0.0}}
        return {"answers": answers, "usage": {"input_tokens": 100}}


# --- the split -------------------------------------------------------------------------------

def test_split_is_stable_and_keeps_a_group_on_one_side():
    items = _items()
    halves = tool.split(items)
    assert halves[tool.DEV] and halves[tool.TEST]
    for g in {it["group"] for it in items}:
        sides = {tool.part_of(it) for it in items if it["group"] == g}
        assert len(sides) == 1
    assert tool.split(items) == halves
    assert tool.is_dev("abc") == tool.is_dev("abc")


# --- the numbers -------------------------------------------------------------------------------

def test_kappa_from_counts_matches_the_repos_two_definitions():
    rng = random.Random(3)
    a = [rng.random() < 0.3 for _ in range(200)]
    b = [x if rng.random() < 0.8 else not x for x in a]
    counts = {}
    for x, y in zip(a, b):
        counts[(x, y)] = counts.get((x, y), 0) + 1
    assert tool.kappa_from(counts) == pytest.approx(tool.mrc.kappa(a, b))
    cats = ["with", "without", "tie"]
    a3 = [rng.choice(cats) for _ in range(200)]
    b3 = [x if rng.random() < 0.6 else rng.choice(cats) for x in a3]
    c3 = {}
    for x, y in zip(a3, b3):
        c3[(x, y)] = c3.get((x, y), 0) + 1
    assert tool.kappa_from(c3) == pytest.approx(tool.mbv._kappa3(a3, b3))
    assert tool.kappa_from({}) is None


def test_auc_from_histograms_matches_brute_force_with_ties():
    rng = random.Random(4)
    pos = [round(rng.random(), 2) for _ in range(50)]
    neg = [round(rng.random() * 0.8, 2) for _ in range(70)]
    brute = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg) / (len(pos) * len(neg))
    hp, hn = {}, {}
    for p in pos:
        hp[tool._bin(p)] = hp.get(tool._bin(p), 0) + 1
    for q in neg:
        hn[tool._bin(q)] = hn.get(tool._bin(q), 0) + 1
    assert tool.auc_from(hp, hn) == pytest.approx(brute)
    assert tool.auc_from({1: 3}, {}) is None


def test_tune_reads_the_threshold_that_separates_the_labels():
    items = []
    for i in range(40):
        yes = i % 2 == 0
        items.append({"id": str(i), "group": str(i), "labels": {R1: yes, R2: yes}})
    ps = {str(i): (0.8 if i % 2 == 0 else 0.6) for i in range(40)}
    t, s = tool.tune(items, ps, tool.QUESTIONS["correction"])
    assert 0.6 < t <= 0.8
    assert s["kappa_jev"] == pytest.approx(1.0)


# --- answers into probabilities ----------------------------------------------------------------

def test_probability_reads_each_answer_type_and_direction():
    noul = {"type": "noul", "criteria": {}}
    assert tool.probability(noul, {"": {"noul": 0.7}}) == pytest.approx(0.7)
    assert tool.probability(dict(noul, invert=True), {"": {"noul": 0.7}}) == pytest.approx(0.3)
    score = {"type": "score", "criteria": ["a", "b", "c"], "invert": True}
    assert tool.probability(score, {"": {"score": 0.5}}) == pytest.approx(0.75)
    assert tool.probability(noul, {"": {"noul": True}}) is None
    assert tool.probability(noul, {}) is None


def test_a_judge_that_always_prefers_reply_one_scores_one_half():
    for v in tool.VARIANTS["better"]:
        biased = {"ab": {"noul": 0.9, "probabilities": {"1": 0.9}},
                  "ba": {"noul": 0.9, "probabilities": {"1": 0.9}}}
        assert tool.probability(v, biased) == pytest.approx(0.5)
        right = {"ab": {"noul": 0.8, "probabilities": {"1": 0.8}},
                 "ba": {"noul": 0.2, "probabilities": {"1": 0.2}}}
        assert tool.probability(v, right) == pytest.approx(0.8)
        assert tool.probability(v, {"ab": right["ab"]}) is None


def test_pairwise_item_is_asked_in_both_orders():
    v = tool.VARIANTS["better"][0]
    qs = tool.questions_for(v, {"subject": ("WITH-REPLY", "WITHOUT-REPLY")})
    assert [s for s, _ in qs] == ["ab", "ba"]
    ab, ba = qs[0][1]["instructions"], qs[1][1]["instructions"]
    assert ab.index("WITH-REPLY") < ab.index("WITHOUT-REPLY")
    assert ba.index("WITHOUT-REPLY") < ba.index("WITH-REPLY")


def test_the_column_changes_with_the_wording():
    v = dict(tool.VARIANTS["correction"][0])
    before = tool.column(v)
    v["instructions"] += " Really."
    assert tool.column(v) != before
    assert tool.column(dict(tool.VARIANTS["correction"][0], name="x")).split("@")[1] == before.split("@")[1]


def test_imported_wordings_are_the_shipped_ones():
    from mnemo.core.reflex import judge

    reflex = [v for v in tool.VARIANTS["relevant"] if v["name"] == "reflex"][0]
    shipped = judge.question("RULE")
    assert reflex["instructions"] + "RULE" == shipped["instructions"]
    assert reflex["criteria"] == shipped["criteria"]
    loss = [v for v in tool.VARIANTS["generic"] if v["name"] == "loss"][0]
    assert loss["instructions"] == tool.mgr.LOSS_INSTRUCTIONS
    assert loss["criteria"] == list(tool.mgr.LOSS_CRITERIA)


# --- requests ------------------------------------------------------------------------------------

def test_requests_share_a_state_and_respect_the_question_cap(monkeypatch):
    monkeypatch.setattr(tool, "MAX_QUESTIONS", 3)
    items = [{"id": "i%d" % i, "state": {"p": "same"}, "state_key": "one", "subject": "r%d" % i}
             for i in range(7)]
    items.append({"id": "other", "state": {"p": "else"}, "state_key": "two", "subject": "x"})
    reqs = tool.requests_for(tool.VARIANTS["correction"][0], items)
    assert [len(r["questions"]) for r in reqs] == [3, 3, 1, 1]
    assert all(r["state"] == {"p": "same"} for r in reqs[:3])
    ids = [iid for r in reqs for iid in r["items"].values()]
    assert sorted(ids) == sorted(it["id"] for it in items)


def test_read_answers_maps_questions_back_to_items():
    v = tool.VARIANTS["correction"][0]
    req = {"items": {"q0": "a", "q1": "b"}}
    got = tool.read_answers(v, req, {"answers": {"q0": {"noul": 0.2}}})
    assert got == {"a": {"p": pytest.approx(0.2)}, "b": {"p": None}}


# --- the protocol -------------------------------------------------------------------------------

def test_dev_first_then_lock_then_test_with_the_locked_wording_only(tmp_path):
    items = _items()
    stub = Stub(items, tmp_path)
    data = tool.run({"correction": items}, tmp_path, stub, workers=1, pause=0, draws=50)
    lock = json.loads((tmp_path / "lock.json").read_text(encoding="utf-8"))
    assert "correction" in lock
    halves = tool.split(items)
    dev_ids = {it["id"] for it in halves[tool.DEV]}
    test_ids = {it["id"] for it in halves[tool.TEST]}
    # every test item was asked only after the lock existed, and exactly once
    test_asks = [(iid, locked) for iid, locked in stub.asked if iid in test_ids]
    assert test_asks and all(locked for _, locked in test_asks)
    assert len(test_asks) == len(test_ids)
    # every wording saw every dev item before the lock
    n_variants = len(tool.VARIANTS["correction"])
    assert sum(1 for iid, locked in stub.asked if iid in dev_ids and not locked) == n_variants * len(dev_ids)
    row = data["rows"][0]
    assert row["status"] == "done"
    assert row["test_summary"]["n"] == len(test_ids)


def test_rerun_sends_nothing_and_never_rewrites_a_lock(tmp_path):
    items = _items()
    tool.run({"correction": items}, tmp_path, Stub(items, tmp_path), workers=1, pause=0, draws=20)
    first = (tmp_path / "lock.json").read_text(encoding="utf-8")
    again = Stub(items, tmp_path)
    data = tool.run({"correction": items}, tmp_path, again, workers=1, pause=0, draws=20)
    assert again.asked == []
    assert data["pending_requests"] == 0
    # an answer changed under the lock: still the same lock
    cache = json.loads((tmp_path / "answers.json").read_text(encoding="utf-8"))
    for col in cache.values():
        for a in col.values():
            a["p"] = 0.01
    (tmp_path / "answers.json").write_text(json.dumps(cache), encoding="utf-8")
    tool.run({"correction": items}, tmp_path, None, draws=20)
    assert (tmp_path / "lock.json").read_text(encoding="utf-8") == first


def test_no_client_sends_nothing_and_reports_pending(tmp_path):
    items = _items()
    data = tool.run({"correction": items}, tmp_path, None, draws=20)
    assert data["rows"][0]["status"] == "pending"
    assert data["pending_requests"] > 0 and data["pending_usd"] > 0
    assert not (tmp_path / "lock.json").exists()


def test_a_judge_that_reads_the_truth_qualifies_and_a_coin_does_not(tmp_path):
    items = _items(n=300, groups=150, agree=0.8)
    (tmp_path / "good").mkdir()
    good = tool.run({"correction": items}, tmp_path / "good", Stub(items, tmp_path / "good"),
                    workers=1, pause=0, draws=100)
    row = good["rows"][0]
    assert row["qualifies"] is True
    assert row["test_summary"]["kappa_jev"] >= row["test_summary"]["kappa_raters"]
    # both raters agree *and* are both wrong on ~6% of agreed items at 80% each
    assert row["test_summary"]["auc"] > 0.8
    (tmp_path / "coin").mkdir()
    coin = tool.run({"correction": items}, tmp_path / "coin", Stub(items, tmp_path / "coin", noise=0.5),
                    workers=1, pause=0, draws=100)
    assert coin["rows"][0]["qualifies"] is False
    lo, hi = coin["rows"][0]["test_ci"]["kappa_jev"]
    assert lo <= coin["rows"][0]["test_summary"]["kappa_jev"] <= hi


def test_a_rejected_request_is_cached_unanswered_and_not_retried(tmp_path):
    items = _items(n=20, groups=10)
    calls = []

    def reject(state, questions):
        calls.append(1)
        raise urllib.error.HTTPError("u", 422, "too long", {}, io.BytesIO(b""))

    data = tool.run({"correction": items}, tmp_path, reject, workers=1, pause=0, draws=10)
    n_first = len(calls)
    assert n_first > 0
    tool.run({"correction": items}, tmp_path, reject, workers=1, pause=0, draws=10)
    cache = json.loads((tmp_path / "answers.json").read_text(encoding="utf-8"))
    assert all(a["p"] is None and a["error"] == 422 for col in cache.values() for a in col.values())
    # every dev item refused: nothing to score, so nothing locked and no test sent
    assert len(calls) == n_first
    assert data["rows"][0]["status"] == "pending"
    assert not (tmp_path / "lock.json").exists()


def test_a_provider_that_is_down_is_retried_later_and_stops_after_max_failures(tmp_path):
    items = _items(n=40, groups=40)
    calls = []

    def down(state, questions):
        calls.append(1)
        raise OSError("connection refused")

    tool.run({"correction": items}, tmp_path, down, workers=1, pause=0, draws=10)
    assert len(calls) == tool.MAX_FAILURES
    assert not (tmp_path / "answers.json").exists()


def test_pairwise_question_end_to_end(tmp_path):
    rng = random.Random(9)
    items = []
    for i in range(80):
        truth = rng.random() < 0.55
        lab = tool.mbv.WITH if truth else tool.mbv.WITHOUT
        items.append({"id": "c%d|0" % i, "group": "g%d" % i, "state": {"developer_message": "m"},
                      "state_key": "c%d" % i, "subject": ("W%03d" % i, "O%03d" % i),
                      "labels": {R1: lab, R2: lab if rng.random() < 0.7 else tool.mbv.TIE}, "truth": truth})

    def answer(q, it, p):
        ins = q["instructions"]
        with_first = ins.index("W%s" % it["id"][1:].split("|")[0].zfill(3)) < ins.index(
            "O%s" % it["id"][1:].split("|")[0].zfill(3))
        p1 = p if with_first else 1 - p
        return {"noul": p1, "probabilities": {"1": p1, "2": 1 - p1, "tie": 0.0}}

    stub = Stub(items, tmp_path, answer=answer)
    stub.by_text = {("W%03d" % i): it for i, it in enumerate(items)}
    data = tool.run({"better": items}, tmp_path, stub, workers=1, pause=0, draws=50)
    row = data["rows"][0]
    assert row["status"] == "done"
    assert row["test_summary"]["auc"] == pytest.approx(1.0)


def test_report_lines_name_the_verdict(tmp_path):
    items = _items(n=200, groups=100)
    data = tool.run({"correction": items}, tmp_path, Stub(items, tmp_path), workers=1, pause=0, draws=30)
    data["cost"]["briefing_raters"] = {}
    text = "\n".join(tool.report_lines(data))
    assert "QUALIFIES" in text and "Jev qualifies for:" in text and "correction" in text


# --- the loaders' item builders ---------------------------------------------------------------------

def test_relevance_items_label_each_shown_rule_and_keep_answered_chunks_only():
    chunks = {"s1": [{"id": "c1", "rules": ["a", "b", "strict1"],
                      "prompts": [{"key": "s1#0", "text": "fix it", "answered": "done"}]}],
              "s2": [{"id": "c2", "rules": ["a"], "prompts": [{"key": "s2#0", "text": "x", "answered": ""}]}]}
    freq = {R1: {"c1": {"s1#0": ["a", "strict1"]}, "c2": {"s2#0": []}},
            R2: {"c1": {"s1#0": ["a"]}}}
    broad, strict = tool.relevance_items(chunks, freq, lambda s: "text of " + s, {"strict1"})
    assert len(broad) == 3  # c2 lacks the second rater
    by = {it["slug"]: it for it in broad}
    assert by["a"]["labels"] == {R1: True, R2: True}
    assert by["strict1"]["labels"] == {R1: True, R2: False}
    assert by["b"]["labels"] == {R1: False, R2: False}
    assert [it["slug"] for it in strict] == ["strict1"]
    assert all(it["group"] == "s1" and it["state_key"] == "s1#0" for it in broad)


def test_redundant_items_read_column_b_and_skip_unanswered_or_empty():
    units = [{"id": "u1", "session_id": "s", "texts": "t", "rule": "r1", "text_b": "B", "empty": False},
             {"id": "u2", "session_id": "s", "texts": "t", "rule": "r2", "text_b": "B", "empty": False},
             {"id": "u3", "session_id": "s", "texts": "t", "rule": "r3", "text_b": "", "empty": True}]
    deliv = {R1: {"u1": {"A": True, "B": False}, "u3": {"A": False, "B": False}},
             R2: {"u1": {"A": False, "B": True}, "u3": {"A": False, "B": False}}}
    items = tool.redundant_items(units, deliv)
    assert [it["id"] for it in items] == ["u1"]
    assert items[0]["labels"] == {R1: False, R2: True}
    assert items[0]["state"] == {"memory_text": "B"}


def test_generic_items_call_generic_and_narrative_junk():
    sample = [{"id": "a", "text": "t1"}, {"id": "b", "text": "t2"}, {"id": "c", "text": "t3"}]
    labels = {"A": {"a": {"cat": "G"}, "b": {"cat": "S"}, "c": {"cat": "N"}},
              "B": {"a": {"cat": "T"}, "b": {"cat": "S"}}}
    items = tool.generic_items(sample, labels)
    assert [(it["id"], it["labels"]) for it in items] == [("a", {"A": True, "B": False}),
                                                          ("b", {"A": False, "B": False})]


def test_better_items_mask_the_rule_and_carry_both_verdicts():
    slug = "app__keep-prs-small"
    arms = {"u1": {"slug": slug, "session_id": "s1", "prompt": "merge it", "claude_md": "",
                   "previous": "I will keep-prs-small here"}}
    answers = {"u1": {"with": [{"text": "per keep-prs-small, split"}, {"text": "w2"}],
                      "without": [{"text": "just merge"}, {"text": "wo2"}]}}
    verdicts = {R1: {"u1|0": {"better": "with"}, "u1|1": {"better": "tie"}},
                R2: {"u1|0": {"better": "without"}}}
    items = tool.better_items(arms, answers, verdicts)
    assert [it["id"] for it in items] == ["u1|0"]
    it = items[0]
    assert it["labels"] == {R1: "with", R2: "without"}
    assert "keep-prs-small" not in it["subject"][0]
    assert "keep-prs-small" not in it["state"]["agent_previous_message"]
    assert it["group"] == "s1"


# --- the briefing set -----------------------------------------------------------------------------

def test_briefing_block_drops_the_header_and_mnemos_other_sections():
    start = ("mnemo://v1 project=app\n[last-briefing session=s0 date=2026-09-20 duration_minutes=5]\n"
             "# Briefing\nShipped the parser.\n[/last-briefing]\n\n[mnemo learned since]\n• x\n")
    assert tool.briefing_block([start]) == "# Briefing\nShipped the parser."
    cut = "[last-briefing session=s]\n# Briefing\nhalf of it\n...\n</persisted-output>"
    assert tool.briefing_block(["other", cut]) == "# Briefing\nhalf of it\n..."
    assert tool.briefing_block(["mnemo://v1 no briefing"]) == ""


def test_typed_prompts_leave_out_what_the_developer_did_not_type():
    prompts = [{"text": "Base directory for this skill: /x\n# Skill"},
               {"text": "Another Claude session sent a message:\n<agent-message>..."},
               {"text": "[Request interrupted by user]"},
               {"text": "ok"},
               {"text": "[Image: source: /tmp/a.png] the button is misaligned"},
               {"text": "please fix the checkout total rounding"}]
    shown, short = tool.typed_prompts(prompts)
    assert shown == ["[image] the button is misaligned", "please fix the checkout total rounding"]
    assert short == 1


def test_briefing_record_and_what_a_rater_sees():
    walked = {"session_start": ["[last-briefing session=s]\n# B\nparser work\n[/last-briefing]"],
              "prompts": [{"text": "continue the parser work please"}, {"text": "ok"}]}
    events = [{"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}},
              {"type": "assistant", "message": {"content": [{"type": "text", "text": "Parser shipped."}]}}]
    rec = tool.briefing_record("sid", walked, events)
    assert rec["briefing"] == "# B\nparser work" and rec["final"] == "Parser shipped."
    prompt = tool.briefing_prompt(rec)
    assert "parser work" in prompt and "continue the parser work" in prompt
    assert "mnemo" not in prompt.lower() and "sid" not in prompt
    assert tool.briefing_record("sid", {"session_start": [], "prompts": walked["prompts"]}, events) is None


def test_parse_briefing_and_items():
    assert tool.parse_briefing('{"about": true, "why": "same issue"}') is True
    assert tool.parse_briefing('```json\n{"about": false}\n```') is False
    assert tool.parse_briefing('{"about": "yes"}') is None
    assert tool.parse_briefing("no json") is None
    recs = [{"id": "s1", "briefing": "B", "prompts": ["p"], "final": "f"},
            {"id": "s2", "briefing": "B", "prompts": ["p"], "final": "f"}]
    items = tool.briefing_items(recs, {R1: {"s1": True, "s2": True}, R2: {"s1": False}})
    assert [(it["id"], it["labels"]) for it in items] == [("s1", {R1: True, R2: False})]


def test_the_briefing_draw_is_seeded_and_bounded():
    ids = ["s%d" % i for i in range(200)]
    first = tool.draw_briefing_sessions(ids)
    assert len(first) == tool.BRIEFING_N and first == tool.draw_briefing_sessions(list(reversed(ids)))
