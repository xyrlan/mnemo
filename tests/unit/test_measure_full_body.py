"""``tools/measure_full_body.py`` over synthetic transcripts, vaults and a
#527 cache built by ``measure_broad_value`` itself (#535)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_full_body as tool  # noqa: E402
from tests.unit import test_measure_broad_value as bvt  # noqa: E402

from mnemo.core import llm  # noqa: E402

BODY = ("keep PRs small: one concern per PR, under 300 changed lines, so review stays quick.\n\n"
        "**Why:** a large PR sat unreviewed for a week in this repo and the one-concern detail is "
        "what reviewers here check first. " + "More context on the why. " * 12)
LINE = "• [[app__small-prs]]: keep PRs small: one concern (call read_mnemo_rule if you need the full file)."


def _page(body, name="small-prs"):
    return ("---\nname: %s\ndescription: d\n---\n%s\n\n> _mnemo stripped a block_\n\n"
            "<!-- mnemo:graph-section -->\n## Related\n- [[other]]\n" % (name, body))


# --- the arm ----------------------------------------------------------------------------------

def test_full_body_is_the_text_the_preview_is_cut_from_uncut():
    from mnemo.core.text_utils import body_preview

    body = tool.full_body(_page(BODY))
    assert body.startswith(body_preview(_page(BODY))) and len(body) > 300
    assert "name:" not in body and "## Related" not in body and "_mnemo" not in body and "More context on the why." in body


def test_full_line_keeps_the_header_and_drops_the_read_suffix():
    assert tool.full_line(LINE, "B\nC") == "• [[app__small-prs]]:\nB\nC"
    assert tool.preview_of(LINE) == "keep PRs small: one concern"


def test_expand_block_replaces_only_the_rules_line_even_under_its_old_slug():
    block = "mnemo reflex context:\n• [[small-prs]]: keep (call read_mnemo_rule …).\n• [[app__other]]: x (call …)."
    new, replaced = tool.expand_block(block, "app__small-prs", "app", "FULL")
    assert replaced == ["• [[small-prs]]: keep (call read_mnemo_rule …)."]
    assert new == "mnemo reflex context:\n• [[small-prs]]:\nFULL\n• [[app__other]]: x (call …)."


def _source(reflex):
    """#527's frozen arms and placed context for a unit carried by ``reflex``
    (``(owner prompt, block)``), with the rule at prompt 1."""
    ctx = {"i": 1, "text": "open the PR", "answered": "", "session_start": [], "native": [], "mcp": [],
           "reflex": reflex, "model": "claude-opus-5-5", "ts": 0}
    row = {"session_id": "s1", "slug": "app__small-prs", "prompts": [1], "reflex": True}
    return tool.bv.build_arms(row, ctx, "app", []), ctx


def test_build_full_changes_the_with_prompt_at_the_rules_lines_only():
    earlier = "mnemo reflex context:\n" + LINE
    now = "mnemo reflex context:\n" + LINE + "\n• [[app__pin-node]]: pin node 20 (call …)."
    arms, ctx = _source([(0, earlier), (1, now), (1, "mnemo reflex context:\n• [[app__lint]]: lint (call …).")])
    body = tool.full_body(_page(BODY))
    got = tool.build_full(arms, ctx, body)
    assert got["replaced"] == 2 and len(got["full_bytes"]) == 2
    assert got["full"] == arms["with"].replace(LINE, "• [[app__small-prs]]:\n" + body)
    assert "pin node 20" in got["full"] and "[[app__lint]]: lint" in got["full"]
    assert got["cut"] and not got["preview_drift"]
    assert got["full_bytes"][0] > got["preview_bytes"][0]


def test_a_rule_rewritten_since_the_session_is_flagged_and_one_that_fits_is_not_cut():
    arms, ctx = _source([(1, "mnemo reflex context:\n" + LINE)])
    rewritten = tool.build_full(arms, ctx, "ARCHIVED 2026-09-23: nobody keeps PRs small any more.")
    assert rewritten["preview_drift"]
    fits = tool.build_full(arms, ctx, "keep PRs small: one concern")
    assert not fits["cut"] and not fits["preview_drift"]


def test_a_block_missing_from_the_with_prompt_is_not_replaced():
    arms, ctx = _source([(1, "mnemo reflex context:\n" + LINE)])
    ctx = dict(ctx, reflex=[(1, "mnemo reflex context:\n" + LINE.replace("one concern", "changed"))])
    assert tool.build_full(arms, ctx, BODY)["replaced"] == 0


# --- the judge --------------------------------------------------------------------------------

def test_order_is_seeded_per_comparison_flipped_for_the_second_rater_and_read_back():
    cids = [tool.comparison_id("u%d" % n, 0) for n in range(40)]
    firsts = [tool.full_first(c, 0) for c in cids]
    assert 5 < sum(firsts) < 35
    assert all(tool.full_first(c, 1) is not f for c, f in zip(cids, firsts))
    for c in cids[:6]:
        for ri in (0, 1):
            one = tool.parse_judge('{"better": "1", "why": "w"}', c, ri)["better"]
            assert one == (tool.FULL if tool.full_first(c, ri) else tool.WITHOUT)
            assert tool.parse_judge('{"better": "Reply 2"}', c, ri)["better"] != one
    assert tool.parse_judge('{"better": "tie"}', cids[0], 0)["better"] == tool.TIE
    assert tool.parse_judge("no json", cids[0], 0) is None


def test_the_rater_sees_neither_rule_nor_which_reply_is_full():
    arms, _ = _source([(1, "mnemo reflex context:\n" + LINE)])
    full = [{"text": "Split it per [[app__small-prs]]."}]
    without = [{"text": "Open one PR."}]
    p = tool.comparison_prompt(arms, full, without, 0, 0)
    assert "app__small-prs" not in p and "keep PRs small" not in p and "full" not in p.lower()
    assert tool.comparison_prompt(arms, full, [], 0, 0) is None


def test_h_full_needs_both_raters_and_a_disagreement_is_a_tie():
    v = {"a": {"u|full|0": {"better": "full"}, "u|full|1": {"better": "full"}},
         "b": {"u|full|0": {"better": "full"}, "u|full|1": {"better": "without"}}}
    assert tool.unit_h(v, "u", ["a", "b"]) == 0.5
    assert tool.unit_h(v, "u", ["a"]) == 1.0
    assert tool.unit_h({"a": {}}, "u", ["a"]) is None


# --- the numbers ------------------------------------------------------------------------------

def test_paired_difference_and_the_preset_bar():
    st = tool.paired([(1.0, 0.0)] * 30 + [(0.0, 0.0)] * 10)
    assert (st["n"], st["full"], st["preview"], st["diff"]) == (40, 0.75, 0.0, 0.75)
    assert st["full_better"] == 30 and st["preview_better"] == 0
    assert st["ci"]["diff"][0] > 0 and tool.verdict(st) == "full body helps"

    def fake(diff, lo, hi):
        return {"n": 1, "diff": diff, "ci": {"diff": [lo, hi]}}
    assert tool.verdict(fake(0.08, 0.01, 0.15)) == "inconclusive"  # CI > 0 but under the bar
    assert tool.verdict(fake(-0.2, -0.3, -0.01)) == "full body hurts"
    assert tool.verdict(fake(-0.03, -0.08, -0.01)) == "full body hurts"  # hurts is read first
    assert tool.verdict(fake(0.0, -0.09, 0.1)) == "no difference"
    assert tool.verdict(fake(0.0, -0.2, 0.2)) == "inconclusive"
    assert tool.verdict({"n": 0}) == "no estimate yet"


def test_quantiles_and_text_groups():
    assert tool.quantiles([10, 20, 30, 40, 50, 60, 70, 80, 90, 100]) == {"n": 10, "median": 55.0, "p90": 100,
                                                                         "max": 100}
    assert tool.text_group("narrative") == "record" and tool.text_group("procedure") == "advice"


# --- end to end -------------------------------------------------------------------------------

def _setup(tmp_path, monkeypatch):
    """A #527 cache made by measure_broad_value itself; ties everywhere, so h(preview) = 0."""
    vault, units_file = bvt._setup(tmp_path)
    (vault / "shared" / "feedback" / "app__small-prs.md").write_text(
        _page(BODY, "app__small-prs"), encoding="utf-8")
    events = bvt._session()
    events[-2] = bvt._hook("UserPromptSubmit", "mnemo reflex context:\n" + LINE)
    Path(json.loads(units_file.read_text(encoding="utf-8"))["sessions"]["s1"]["path"]).write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    def provider(prompt, *, system, model, timeout):
        if system == tool.bv.LOCATE_SYSTEM:
            return bvt._resp('{"lines": []}')
        if system == tool.rl.ARM_SYSTEM:
            return bvt._resp("reply")
        return bvt._resp('{"better": "tie", "why": "same"}')
    monkeypatch.setattr(llm, "resolve", lambda cfg: provider)
    src = tmp_path / "bv"
    assert tool.bv.main(["--vault", str(vault), "--units", str(units_file), "--out", str(src),
                         "--rater", "m1", "--rater", "m2", "--pause", "0", "--workers", "1", "--send", "--json"]) == 0
    return vault, units_file, src


def test_main_answers_the_full_arm_judges_it_and_a_rerun_asks_nothing(tmp_path, capsys, monkeypatch):
    vault, units_file, src = _setup(tmp_path, monkeypatch)
    capsys.readouterr()
    source_before = {p.name: p.read_bytes() for p in src.iterdir()}
    base = ["--vault", str(vault), "--units", str(units_file), "--source", str(src),
            "--out", str(tmp_path / "out"), "--rater", "m1", "--rater", "m2", "--pause", "0", "--workers", "1"]
    monkeypatch.setattr(llm, "resolve", lambda cfg: pytest.fail("a dry run must not resolve a provider"))
    assert tool.main(base + ["--json"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    # fridays came by SessionStart only: small-prs is the one reflex unit
    assert (data["units"], data["built"], data["consistent"], data["judged"]) == (1, 1, 1, 0)
    assert "claude-fable-5-1: pending 2 answer call(s)" in captured.err

    asked = []

    def provider(prompt, *, system, model, timeout):
        asked.append((model, system))
        if system == tool.rl.ARM_SYSTEM:
            assert "More context on the why." in prompt and "(call read_mnemo_rule if you need" not in prompt
            return bvt._resp("one concern per PR, as the detail says")
        assert system == tool.bv.JUDGE_SYSTEM and "More context on the why." not in prompt
        one = "detail" in prompt.split("## Reply 1")[1].split("## Reply 2")[0]
        return bvt._resp(json.dumps({"better": "1" if one else "2", "why": "fits"}))
    monkeypatch.setattr(llm, "resolve", lambda cfg: provider)
    assert tool.main(base + ["--send", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [m for m, s in asked if s == tool.rl.ARM_SYSTEM] == ["claude-fable-5-1"] * 2
    assert sum(s == tool.bv.JUDGE_SYSTEM for _, s in asked) == 4
    both = data["results"]["both"]
    assert (both["n"], both["full"], both["preview"], both["diff"]) == (1, 1.0, 0.0, 1.0)
    assert data["shares"]["share_full"] == 1.0 and data["agreement"]["same"] == 2
    assert set(data["breakdowns"]["page type"]) == {"feedback"}
    assert data["price"]["full"]["median"] > data["price"]["preview"]["median"]
    assert data["cost"]["m1"]["calls"] == 2
    # #527's cache is read, never written
    assert {p.name: p.read_bytes() for p in src.iterdir()} == source_before

    asked.clear()
    assert tool.main(base + ["--send", "--json"]) == 0
    assert asked == [] and json.loads(capsys.readouterr().out)["judged"] == 1
    assert tool.main(base) == 0
    out = capsys.readouterr().out
    assert "h(full)     +1.000" in out and "median" in out
    assert "VERDICT (both raters): FULL BODY HELPS" in out
