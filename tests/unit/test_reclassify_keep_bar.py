"""reclassify keep bar (#119): a keep needs a specific quote and a stated link."""
from __future__ import annotations

from mnemo.core import reclassify
from mnemo.core.reclassify_types import RuleDoc, Verdict


def _rule(tmp_path, slug="r"):
    src = "bots/p/briefings/sessions/s.md"
    b = tmp_path / src
    b.parent.mkdir(parents=True, exist_ok=True)
    b.write_text(
        "## Corrections\n"
        "- \"vamo mudar o env do app para prod e subir\" → env\n"
        "- \"implementa os fixes\" → fixes\n",
        encoding="utf-8",
    )
    return RuleDoc(path=tmp_path / "shared/feedback/r.md", slug=slug, name="R", fm={}, body="", sources=[src])


def test_generic_quote_is_demoted(tmp_path):
    rule = _rule(tmp_path)
    v = Verdict(slug="r", verdict="keep", quote="implementa os fixes", source=rule.sources[0], link="user asked")
    [out] = reclassify.validate([v], {"r": rule}, tmp_path, projects_root=tmp_path / "none")
    assert out.verdict == "demote" and out.reason == "quote-generic"


def test_missing_link_is_demoted(tmp_path):
    rule = _rule(tmp_path)
    v = Verdict(slug="r", verdict="keep", quote="vamo mudar o env do app para prod e subir", source=rule.sources[0], link="")
    [out] = reclassify.validate([v], {"r": rule}, tmp_path, projects_root=tmp_path / "none")
    assert out.verdict == "demote" and out.reason == "link-missing"


def test_specific_quote_with_link_is_kept(tmp_path):
    rule = _rule(tmp_path)
    v = Verdict(slug="r", verdict="keep", quote="vamo mudar o env do app para prod e subir",
                source=rule.sources[0], link="user ordered the prod env switch")
    [out] = reclassify.validate([v], {"r": rule}, tmp_path, projects_root=tmp_path / "none")
    assert out.verdict == "keep" and out.link == "user ordered the prod env switch"


def test_plan_json_round_trips_link(tmp_path):
    plan = reclassify.Plan(run_id="20260902T000000", llm_calls=1, verdicts=[
        Verdict(slug="r", verdict="keep", quote="vamo mudar o env do app para prod e subir",
                source="bots/p/briefings/sessions/s.md", link="user ordered the prod env switch",
                path="shared/feedback/r.md"),
        Verdict(slug="d", verdict="demote", reason="quote-generic"),
    ])
    reclassify.save_plan(tmp_path, plan)
    loaded = reclassify.load_plan(tmp_path)
    assert loaded is not None
    assert loaded.verdicts[0].link == "user ordered the prod env switch"
    assert loaded.verdicts[1].link is None


def test_parse_verdicts_reads_link():
    text = ('{"verdicts": [{"slug": "r", "verdict": "keep", "quote": "q", "source": "s", '
            '"link": "the user ordered it", "reason": ""}]}')
    [v] = reclassify.parse_verdicts(text)
    assert v.link == "the user ordered it"


def test_prompt_demands_link():
    assert "`link`" in reclassify.RECLASSIFY_SYSTEM_PROMPT
    assert '"link": ...' in reclassify.RECLASSIFY_SYSTEM_PROMPT
