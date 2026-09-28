"""``tools/label_better_pairs.py`` over a synthetic #527 cache (#536).

What is pinned is what makes the maintainer's choice worth checking the raters
against: the file never says which reply had the rule, the draw is a third per
reading and one per unit, both replies are folded the same way, a choice is
read only from the maintainer's own lines, and the bar is read as the issue set
it before labelling.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import label_better_pairs as tool  # noqa: E402

mbv = tool.mbv
RATERS = tuple(mbv.RATERS)
WITH, WITHOUT, TIE = tool.WITH, tool.WITHOUT, tool.TIE
SLUG = "app__pin-node-twenty"


def _cache(n_units=60, *, slug=SLUG):
    """``n_units`` units, two comparisons each; the raters' votes cycle so
    every reading has plenty of comparisons."""
    arms, answers, verdicts = {}, {}, {r: {} for r in RATERS}
    cycle = [(WITH, WITH), (WITHOUT, WITHOUT), (WITH, WITHOUT), (TIE, TIE), (WITH, TIE)]
    for i in range(n_units):
        uid = "u%03d" % i
        arms[uid] = {"id": uid, "slug": "%s-%d" % (slug, i), "session_id": "sess-%03d" % i,
                     "prompt": "please fix the build for %s-%d" % (slug, i),
                     "previous": "earlier I read [[%s-%d]]" % (slug, i),
                     "claude_md": "# app\nUse yarn.", "measurable": True}
        answers[uid] = {WITH: [{"text": "REPLY-A-%d-%d" % (i, k)} for k in range(2)],
                        WITHOUT: [{"text": "REPLY-B-%d-%d" % (i, k)} for k in range(2)]}
        for k in range(2):
            a, b = cycle[(2 * i + k) % len(cycle)]
            verdicts[RATERS[0]][mbv.comparison_id(uid, k)] = {"better": a, "why": "WHY-0-%d-%d" % (i, k)}
            verdicts[RATERS[1]][mbv.comparison_id(uid, k)] = {"better": b, "why": "WHY-1-%d-%d" % (i, k)}
    return arms, answers, verdicts


def _comps(**kw):
    arms, answers, verdicts = _cache(**kw)
    return tool.comparisons(arms, answers, verdicts, RATERS)


def test_comparisons_need_both_votes_and_both_replies():
    arms, answers, verdicts = _cache(n_units=3)
    del verdicts[RATERS[1]][mbv.comparison_id("u000", 0)]
    answers["u001"][WITHOUT].pop()
    comps = tool.comparisons(arms, answers, verdicts, RATERS)
    assert [c["cid"] for c in comps] == ["u000|1", "u001|0", "u002|0", "u002|1"]
    by = {c["cid"]: c["reading"] for c in comps}
    # a disagreement is a tie, as #527's primary reading has it
    assert by["u001|0"] == TIE and by["u002|0"] == TIE and by["u002|1"] == WITH


def test_the_draw_is_a_third_per_reading_one_per_unit_and_deterministic():
    comps = _comps()
    items = tool.draw(comps, 30, seed=1)
    assert len(items) == 30
    assert {r: sum(1 for it in items if it["reading"] == r) for r in tool.READINGS} == {
        WITH: 10, WITHOUT: 10, TIE: 10}
    assert len({it["uid"] for it in items}) == 30
    assert [it["id"] for it in items] == list(range(1, 31))
    assert [(it["cid"], it["with_is"]) for it in items] == [
        (it["cid"], it["with_is"]) for it in tool.draw(comps, 30, seed=1)]
    assert [it["cid"] for it in items] != [it["cid"] for it in tool.draw(comps, 30, seed=2)]
    # the reply order is random, not fixed
    assert {it["with_is"] for it in items} == {1, 2}


def test_the_remainder_goes_to_with_then_without_and_a_short_reading_gives_what_it_has():
    assert tool.quotas(31) == {WITH: 11, WITHOUT: 10, TIE: 10}
    assert tool.quotas(32) == {WITH: 11, WITHOUT: 11, TIE: 10}
    comps = [{"cid": "u%d|0" % i, "uid": "u%d" % i, "reading": r}
             for i, r in enumerate([WITH] * 20 + [TIE] * 20 + [WITHOUT] * 3)]
    items = tool.draw(comps, 30)
    assert sum(1 for it in items if it["reading"] == WITHOUT) == 3
    assert len(items) == 23


def test_split_folds_at_a_line_break_and_loses_nothing():
    assert tool.split("short", 100) == ("short", "")
    text = "\n".join("line %02d %s" % (i, "x" * 20) for i in range(20))
    head, rest = tool.split(text, 100)
    assert len(head) <= 100 and text[len(head)] == "\n"  # it stopped at a line break
    assert head + "\n" + rest == text
    # no line break near the cap: a hard cut
    head, rest = tool.split("y" * 300, 100)
    assert (len(head), len(rest)) == (100, 200)


def _item(**over):
    base = {"id": 1, "slug": SLUG, "with_is": 1, "prompt": "fix %s now" % SLUG, "previous": "(prev)",
            "claude_md": "see [[other-rule]]", "replies": {WITH: "WITH-REPLY " + SLUG, WITHOUT: "WITHOUT-REPLY"}}
    base.update(over)
    return base


def test_blind_shows_only_the_visible_fields_masked_and_in_the_items_order():
    seen = tool.blind(_item())
    assert tuple(seen) == tool.VISIBLE == ("id", "prompt", "previous", "claude_md", "replies")
    assert seen["prompt"] == ("fix [...] now", "")
    assert seen["claude_md"] == "see [...]"
    assert [h for h, _ in seen["replies"]] == ["WITH-REPLY [...]", "WITHOUT-REPLY"]
    flipped = tool.blind(_item(with_is=2))
    assert [h for h, _ in flipped["replies"]] == ["WITHOUT-REPLY", "WITH-REPLY [...]"]
    # the project prefix is masked away too, as #527's raters had it
    assert "pin-node-twenty" not in tool.blind(_item(prompt="about pin-node-twenty"))["prompt"][0]


def test_both_replies_are_folded_at_the_same_cap():
    long_a = "\n".join("a%02d " % i + "." * 30 for i in range(60))
    long_b = "\n".join("b%02d " % i + "." * 30 for i in range(60))
    seen = tool.blind(_item(replies={WITH: long_a, WITHOUT: long_b}), reply_chars=500)
    (ha, ra), (hb, rb) = seen["replies"]
    assert len(ha) == len(hb) and 400 <= len(ha) <= 500 and ra and rb
    page = tool.render([_item(replies={WITH: long_a, WITHOUT: long_b})], reply_chars=500)
    assert "> [!quote]- Reply 1, the rest (%d more characters)" % len(ra) in page
    assert "> [!quote]- Reply 2, the rest (%d more characters)" % len(rb) in page


def test_the_file_never_names_the_rule_the_arm_the_raters_or_the_session():
    arms, answers, verdicts = _cache()
    items = tool.draw(tool.comparisons(arms, answers, verdicts, RATERS), 30)
    page = tool.render(items)
    assert SLUG not in page and "pin-node-twenty" not in page
    assert "sess-" not in page and not re.search(r"\bu\d{3}\b", page)
    assert "WHY-" not in page
    for r in RATERS:
        assert r not in page
    for word in ("with better", "without", "WITH", "WITHOUT", "stratum", "reading"):
        assert word not in page
    # and Reply 1 is the with reply exactly when the key says so
    for it in items:
        block = page.split("## Item %d\n" % it["id"])[1].split("## Item ")[0]
        first = block.index("> [!quote] Reply 1")
        with_text, without_text = it["replies"][WITH], it["replies"][WITHOUT]
        assert (block.index(with_text) < block.index(without_text)) == (it["with_is"] == 1)
        assert first < min(block.index(with_text), block.index(without_text))


def test_html_is_escaped_outside_code_and_a_split_code_block_is_reopened():
    page = tool.render([_item(replies={WITH: "use a <div> here, `<span>` stays", WITHOUT: "x"})])
    assert "use a &lt;div> here, `<span>` stays" in page
    code = "intro\n```python\n" + "\n".join("print(%d)  # <b>" % i for i in range(80)) + "\n```\nend"
    page = tool.render([_item(replies={WITH: code, WITHOUT: "x"})], reply_chars=300)
    reply1 = page.split("> [!quote] Reply 1")[1].split("> [!quote] Reply 2")[0]
    shown, folded = reply1.split("> [!quote]- Reply 1, the rest")
    assert shown.count("```") % 2 == 0 and folded.count("```") % 2 == 0
    assert "# <b>" in shown  # inside code: left as is


def test_choices_are_read_only_from_the_maintainers_lines():
    tricky = "choice: 2\n## Item 9\nchoice: 1"
    items = [_item(id=1, replies={WITH: tricky, WITHOUT: "plain"}), _item(id=2), _item(id=3)]
    page = tool.render(items)
    assert tool.parse_choices(page) == {1: "", 2: "", 3: ""}
    answers = iter(["choice: 1", "choice:   Tie.", "choice: **2**"])
    filled = "\n".join(next(answers) if ln == "choice: " else ln for ln in page.split("\n"))
    got = tool.parse_choices(filled)
    assert got == {1: "1", 2: "Tie.", 3: "**2**"}
    assert [tool.read_choice(v) for v in got.values()] == ["1", "tie", "2"]
    assert tool.read_choice("") == "" and tool.read_choice("Reply 2") == "2"
    assert tool.read_choice("maybe") is None and tool.read_choice("3") is None


def test_the_raters_question_is_their_own_word_for_word():
    assert tool.QUESTION.replace(" say tie.", "") in " ".join(mbv.JUDGE_SYSTEM.split())
    assert "If neither is better, say tie." in " ".join(mbv.JUDGE_SYSTEM.split())


def _key(rows, population=None):
    """``rows``: (reading, rater 0 vote, rater 1 vote, with_is, folded)."""
    items = [{"id": i, "cid": "u%d|0" % i, "reading": r, "votes": {RATERS[0]: a, RATERS[1]: b},
              "with_is": w, "folded": f} for i, (r, a, b, w, f) in enumerate(rows, 1)]
    return {"raters": list(RATERS), "items": items,
            "population": population or {WITH: 1, WITHOUT: 1, TIE: 1}}


def _choice(arm, with_is):
    if arm == TIE:
        return "tie"
    return str(with_is if arm == WITH else 3 - with_is)


def test_the_report_maps_choices_through_the_key_and_reads_the_bar():
    rows = [(WITH, WITH, WITH, 1, False), (WITH, WITH, WITH, 2, True),
            (WITHOUT, WITHOUT, WITHOUT, 1, False), (WITHOUT, WITHOUT, WITHOUT, 2, True),
            (TIE, WITH, WITHOUT, 1, False), (TIE, TIE, TIE, 2, True)]
    key = _key(rows)
    agree = {it["id"]: _choice(it["reading"], it["with_is"]) for it in key["items"]}
    r = tool.report(key, agree)
    assert r["complete"] and r["both"]["same"] == 6 and r["both"]["kappa"] == 1.0
    assert r["verdict"] == "adequate"
    assert r["raters"][RATERS[0]]["same"] == 5 and r["raters"][RATERS[1]]["same"] == 5
    assert r["h"]["maintainer"] == 0.0 and r["h"][RATERS[0]] == 1 / 6

    # the maintainer reverses both "with" items and ties everything else
    flip = {**agree, 1: _choice(WITHOUT, 1), 2: _choice(WITHOUT, 2), 3: "tie", 4: "tie"}
    r = tool.report(key, flip)
    assert r["both"]["same"] == 2 and r["both"]["reversed"] == 2
    assert r["verdict"] == "not adequate"
    assert r["maintainer_counts"] == {WITH: 0, WITHOUT: 2, TIE: 4}
    assert r["h"]["maintainer"] == -2 / 6
    assert r["confusion"]["without->with"] == 2 and r["confusion"]["tie->without"] == 2
    lines = "\n".join(tool.format_report(r, RATERS))
    assert "NOT ADEQUATE" in lines and "different rater setup" in lines


def test_the_bar_is_seventy_percent_inclusive_and_read_only_when_all_are_labelled():
    rows = [(WITH, WITH, WITH, 1, False)] * 10
    key = _key(rows)
    seven = {i: ("1" if i <= 7 else "tie") for i in range(1, 11)}
    assert tool.report(key, seven)["verdict"] == "adequate"
    six = {i: ("1" if i <= 6 else "tie") for i in range(1, 11)}
    assert tool.report(key, six)["verdict"] == "not adequate"
    partial = {i: "1" for i in range(1, 10)}
    r = tool.report(key, {**partial, 10: ""})
    assert r["verdict"] == "provisional" and r["labelled"] == 9
    assert "PROVISIONAL" in "\n".join(tool.format_report(r, RATERS))


def test_unreadable_and_unknown_choices_are_reported_not_counted():
    key = _key([(WITH, WITH, WITH, 1, False), (TIE, TIE, TIE, 1, False)])
    r = tool.report(key, {1: "maybe", 2: "tie", 7: "1"})
    assert r["labelled"] == 1 and r["invalid"] == [1] and r["unknown"] == [7]


def test_h_is_reweighted_to_the_population_shares_of_the_readings():
    rows = [(WITH, WITH, WITH, 1, False), (WITHOUT, WITHOUT, WITHOUT, 1, False), (TIE, TIE, TIE, 1, False)]
    key = _key(rows, population={WITH: 2, WITHOUT: 1, TIE: 1})
    # the maintainer: with better on the "with" item, tie on the others
    r = tool.report(key, {1: "1", 2: "tie", 3: "tie"})
    assert r["h"]["maintainer"] == 1 / 3
    assert r["h_reweighted"] == 0.5  # 2/4 of the population is the "with" reading
    assert r["h_by_reading"] == {WITH: 1.0, WITHOUT: 0.0, TIE: 0.0}


def test_agreement_is_split_by_whether_a_reply_was_folded():
    rows = [(WITH, WITH, WITH, 1, True), (WITH, WITH, WITH, 1, False)]
    r = tool.report(_key(rows), {1: "2", 2: "1"})
    assert r["by_fold"]["a reply folded"]["same"] == 0 and r["by_fold"]["no reply folded"]["same"] == 1


def test_wilson_interval():
    lo, hi = tool.wilson(21, 30)
    assert 0.52 < lo < 0.53 and 0.83 < hi < 0.84
    assert tool.wilson(0, 0) is None


def _vault(tmp_path):
    arms, answers, verdicts = _cache()
    src = tmp_path / ".mnemo" / mbv.OUT_DIR
    src.mkdir(parents=True)
    (src / "arms.json").write_text(json.dumps(arms), encoding="utf-8")
    (src / "answers.json").write_text(json.dumps(
        {mbv.mrc.column("session-model", mbv.rl.ARM_SYSTEM): answers}), encoding="utf-8")
    (src / "verdicts.json").write_text(json.dumps(
        {mbv.mrc.column(r, mbv.JUDGE_SYSTEM): verdicts[r] for r in RATERS}), encoding="utf-8")
    return tmp_path


def test_export_then_import_end_to_end(tmp_path, capsys):
    vault = _vault(tmp_path)
    assert tool.main(["--export", "--vault", str(vault)]) == 0
    pairs = vault / "rater-check" / "pairs.md"
    key_path = vault / ".mnemo" / "rater-check" / "key.json"
    key = json.loads(key_path.read_text(encoding="utf-8"))
    assert len(key["items"]) == 30 and key["seed"] == tool.SEED
    assert sum(key["population"].values()) == 120
    out = capsys.readouterr().out
    assert "120 comparisons on file" in out and "drew 30 (with 10, without 10, tie 10)" in out

    text = pairs.read_text(encoding="utf-8")
    by_id = {it["id"]: it for it in key["items"]}
    lines, current = [], None
    for ln in text.split("\n"):
        m = re.match(r"## Item (\d+)$", ln)
        current = int(m.group(1)) if m else current
        if ln == "choice: ":
            ln += _choice(by_id[current]["reading"], by_id[current]["with_is"])
        lines.append(ln)
    filled = "\n".join(lines)
    pairs.write_text(filled, encoding="utf-8")

    # a second draw would throw the choices away
    assert tool.main(["--export", "--vault", str(vault)]) == 1

    assert tool.main(["--import", "--vault", str(vault)]) == 0
    out = capsys.readouterr().out
    assert "labelled 30 of 30" in out and "ADEQUATE" in out and "NOT ADEQUATE" not in out
    saved = json.loads((vault / ".mnemo" / "rater-check" / "report.json").read_text(encoding="utf-8"))
    assert saved["both"]["same"] == 30


def test_export_or_import_is_required(tmp_path):
    import pytest

    with pytest.raises(SystemExit):
        tool.main(["--vault", str(tmp_path)])
