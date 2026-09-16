"""friction.candidates.rank + friction.link.resolve — the contradiction pass.

The two model replies below were recorded 2026-09-16 from ``claude-haiku-4-5``
through ``llm.call`` with ``CONTRADICTION_PROMPT``, against the three-rule
vault :func:`_merge_vault` builds — fences and all, exactly as the CLI
returned them. No test here spawns a model.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mnemo.core import llm
from mnemo.core.corrections import Correction
from mnemo.core.extract.prompts import existing_rules
from mnemo.core.extract.prompts.templates import contradiction
from mnemo.core.friction import candidates, link
from mnemo.core.friction.candidates import CONTRADICTION_CANDIDATES, Candidate, rank
from mnemo.core.friction.ledger import FrictionRecord
from mnemo.core.friction.link import LinkResult, resolve
from mnemo.core.reflex import index as reflex_index
from mnemo.core.reflex.tokenizer import tokenize_query

RECORDED_CONTRADICTION = (
    "```json\n{\n  \"links\": [\n    {\n      \"slug\": \"merge-requires-admin\",\n"
    "      \"relation\": \"contradicts\",\n      \"why\": \"The rule requires --admin for "
    "master merges; the user correction says --admin is not needed and gh pr merge "
    "runs directly.\"\n    }\n  ]\n}\n```"
)
RECORDED_REFINEMENT = (
    "```json\n{\"links\": [{\"slug\": \"merge-requires-admin\", \"relation\": \"refines\", "
    "\"why\": \"User adds a condition (wait for Windows check) to the merge step; the rule "
    "stays valid but now has a prerequisite.\"}]}\n```"
)

CONTRADICTION = Correction(
    quote="não precisa de --admin no merge, roda o gh pr merge direto",
    rule="Run gh pr merge directly; --admin is not required to merge into master.",
)
REFINEMENT = Correction(
    quote="antes de mergear espera o check do windows também, ele é continue-on-error",
    rule="Before merging, also wait for the Windows check, even though it is continue-on-error.",
)


def _write_rule(
    vault: Path,
    slug: str,
    body: str,
    *,
    page_type: str = "feedback",
    sources: list[str] | None = None,
    name: str | None = None,
    stability: str = "stable",
    subdir: str | None = None,
) -> Path:
    sources = sources or ["bots/mnemo/memory/{}.md".format(slug)]
    path = vault / "shared" / (subdir or page_type) / "{}.md".format(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "name: {}\n"
        "description: d\n"
        "tags:\n  - git\n"
        "sources:\n{}"
        "stability: {}\n"
        "---\n{}\n".format(
            name or slug,
            "".join("  - {}\n".format(s) for s in sources),
            stability,
            body,
        ),
        encoding="utf-8",
    )
    return path


def _merge_vault(vault: Path) -> Path:
    _write_rule(vault, "merge-requires-admin",
                "Merging a PR into master requires `gh pr merge --admin`, because master "
                "needs code-owner approval and a plain merge is refused.")
    _write_rule(vault, "gate-merge-on-checks",
                "Before running gh pr merge, wait for gh pr checks to pass; gh pr merge "
                "does not gate on checks by itself and will merge a PR with a red job.")
    _write_rule(vault, "commit-messages-in-english",
                "Write commit messages and PR descriptions in English, even when the "
                "conversation is in Portuguese.")
    return vault


class _Recorded:
    """A runner that replays one reply and remembers what it was asked."""

    def __init__(self, reply):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def __call__(self, prompt, *, system):
        self.calls.append((prompt, system))
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


def _cands(*slugs: str) -> list[Candidate]:
    return [Candidate(slug=s, name=s, body="body of " + s, score=1.0, rank=i + 1)
            for i, s in enumerate(slugs)]


# --- rank -----------------------------------------------------------------


def test_rank_returns_candidates_with_bodies_best_first(tmp_vault):
    _merge_vault(tmp_vault)
    got = rank(tmp_vault, CONTRADICTION, project="mnemo")
    assert [c.slug for c in got][0] == "merge-requires-admin"
    assert [c.rank for c in got] == list(range(1, len(got) + 1))
    assert got == sorted(got, key=lambda c: -c.score)
    top = got[0]
    assert "gh pr merge --admin" in top.body
    assert "---" not in top.body and "sources:" not in top.body


def test_rank_reaches_past_a_source_count_ordered_top_80(tmp_vault):
    """The preamble's measurement, pinned: the hint's cut is not this pool.

    Ninety rules with two sources each outrank the target by ``source_count``,
    and its slug sorts last, so a ``source_count``-then-slug top-80 — the
    ``existing_rules`` ordering — can never show it. ``rank`` scores the whole
    pool and returns it.
    """
    for i in range(90):
        _write_rule(tmp_vault, "aa-filler-{:02d}".format(i),
                    "Keep module {} small and readable.".format(i),
                    sources=["bots/mnemo/memory/f{}.md".format(i),
                             "bots/mnemo/memory/g{}.md".format(i)])
    _write_rule(tmp_vault, "zz-merge-requires-admin",
                "Merging into master requires gh pr merge --admin.")

    existing_rules.clear_cache()
    hint = existing_rules.existing_rules_fragment(tmp_vault, "feedback", agents={"mnemo"})
    assert "aa-filler-00" in hint
    assert "zz-merge-requires-admin" not in hint

    got = rank(tmp_vault, CONTRADICTION, project="mnemo")
    assert got and got[0].slug == "zz-merge-requires-admin"


def test_rank_caps_at_contradiction_candidates(tmp_vault):
    assert CONTRADICTION_CANDIDATES == 40
    for i in range(CONTRADICTION_CANDIDATES + 15):
        _write_rule(tmp_vault, "merge-rule-{:02d}".format(i),
                    "gh pr merge note number {}.".format(i))
    got = rank(tmp_vault, CONTRADICTION, project="mnemo")
    assert len(got) == CONTRADICTION_CANDIDATES
    assert len(rank(tmp_vault, CONTRADICTION, project="mnemo", limit=5)) == 5


def test_rank_pool_is_what_candidates_for_project_admits(tmp_vault):
    _merge_vault(tmp_vault)
    _write_rule(tmp_vault, "other-project-merge", "gh pr merge --admin elsewhere.",
                sources=["bots/clubinho/memory/x.md"])
    _write_rule(tmp_vault, "universal-merge", "gh pr merge --admin everywhere.",
                sources=["bots/a/memory/x.md", "bots/b/memory/y.md"])
    _write_rule(tmp_vault, "evolving-merge", "gh pr merge --admin maybe.",
                stability="evolving")
    _write_rule(tmp_vault, "draft-merge", "gh pr merge --admin draft.",
                subdir="_inbox/feedback")

    slugs = {c.slug for c in rank(tmp_vault, CONTRADICTION, project="mnemo")}
    assert "universal-merge" in slugs
    assert "merge-requires-admin" in slugs
    assert not slugs & {"other-project-merge", "evolving-merge", "draft-merge"}


def test_rank_filter_is_inherited_from_candidates_for_project(tmp_vault, monkeypatch):
    """Whatever the chokepoint drops — a retired rule, once retire lands — rank drops."""
    _merge_vault(tmp_vault)
    real = candidates.candidates_for_project
    monkeypatch.setattr(
        candidates, "candidates_for_project",
        lambda idx, project: [s for s in real(idx, project) if s != "merge-requires-admin"],
    )
    slugs = [c.slug for c in rank(tmp_vault, CONTRADICTION, project="mnemo")]
    assert "merge-requires-admin" not in slugs
    assert slugs


def test_rank_reads_the_on_disk_index_and_skips_a_gone_page(tmp_vault):
    _merge_vault(tmp_vault)
    reflex_index.write_index(tmp_vault, reflex_index.build_index(tmp_vault))
    (tmp_vault / "shared" / "feedback" / "merge-requires-admin.md").unlink()
    # Added after the index was written: the on-disk index does not know it.
    _write_rule(tmp_vault, "late-merge-admin", "gh pr merge --admin --admin --admin.")

    got = rank(tmp_vault, CONTRADICTION, project="mnemo")
    slugs = [c.slug for c in got]
    assert "merge-requires-admin" not in slugs
    assert "late-merge-admin" not in slugs
    assert slugs[0] == "gate-merge-on-checks" and got[0].rank == 1


def test_rank_accepts_a_passed_index_and_a_ledger_record(tmp_vault):
    _merge_vault(tmp_vault)
    idx = reflex_index.build_index(tmp_vault)
    rec = FrictionRecord(session_id="s", project="mnemo",
                         quote=CONTRADICTION.quote, rule_text=CONTRADICTION.rule)
    got = rank(tmp_vault, rec, project="mnemo", index=idx)
    assert got[0].slug == "merge-requires-admin"


def test_rank_finds_a_page_whose_stem_is_not_its_slug(tmp_vault):
    path = _write_rule(tmp_vault, "file-stem", "gh pr merge needs --admin.",
                       name="Merge Needs Admin")
    got = rank(tmp_vault, CONTRADICTION, project="mnemo")
    assert [(c.slug, c.name) for c in got] == [("Merge Needs Admin", "Merge Needs Admin")]
    assert path.exists()


def test_rank_empty_cases(tmp_vault):
    assert rank(tmp_vault, CONTRADICTION, project="mnemo") == []  # empty vault
    _merge_vault(tmp_vault)
    assert rank(tmp_vault, Correction(quote="", rule=""), project="mnemo") == []
    assert rank(tmp_vault, CONTRADICTION, project="nobody") == []
    assert rank(tmp_vault, CONTRADICTION, project="mnemo", limit=0) == []


# --- resolve --------------------------------------------------------------


def test_semantic_contradiction_in_near_identical_vocabulary_resolves(tmp_vault):
    """merge-requires-admin: the correction reuses the rule's own words.

    Paired with the refinement test below, the same slug in the same
    vocabulary comes out linked or not on the model's relation alone — which
    no lexical score can decide.
    """
    _merge_vault(tmp_vault)
    cands = rank(tmp_vault, CONTRADICTION, project="mnemo")
    shared = set(tokenize_query(CONTRADICTION.rule)) & set(tokenize_query(cands[0].body))
    assert {"gh", "pr", "merge", "--admin", "master"} <= shared

    runner = _Recorded(RECORDED_CONTRADICTION)
    result = resolve(CONTRADICTION, cands, runner=runner)

    assert result.contradicts == ["merge-requires-admin"]
    assert result.link_basis == "extractor"
    assert result.raw["links"][0]["relation"] == "contradicts"
    prompt, system = runner.calls[0]
    assert system == contradiction.CONTRADICTION_PROMPT
    assert CONTRADICTION.quote in prompt
    for c in cands:
        assert "### {} — ".format(c.slug) in prompt


def test_refinement_does_not_link(tmp_vault):
    _merge_vault(tmp_vault)
    cands = rank(tmp_vault, REFINEMENT, project="mnemo")
    assert "merge-requires-admin" in {c.slug for c in cands}

    result = resolve(REFINEMENT, cands, runner=_Recorded(RECORDED_REFINEMENT))
    assert result == LinkResult(contradicts=[], link_basis="none",
                                raw=llm._parse_llm_json(RECORDED_REFINEMENT))


@pytest.mark.parametrize("reply", [
    "I think merge-requires-admin is contradicted.",
    '{"links": [{"slug": "merge-requires-admin", "relation": "contradicts"',
    '{"contradicts": ["merge-requires-admin"]}',
    '{"links": "merge-requires-admin"}',
    '["merge-requires-admin"]',
    "",
    None,
    42,
    llm.LLMTimeoutError("subprocess timed out twice after 60s"),
    llm.LLMSubprocessError("claude CLI not found; install Claude Code first"),
    RuntimeError("anything at all"),
])
def test_malformed_or_failed_pass_degrades_to_none(reply):
    result = resolve(CONTRADICTION, _cands("merge-requires-admin"), runner=_Recorded(reply))
    assert result.contradicts == []
    assert result.link_basis == "none"
    assert isinstance(result.raw, dict)


def test_malformed_links_are_skipped_one_by_one():
    reply = {"links": [
        "merge-requires-admin",
        {"slug": 7, "relation": "contradicts"},
        {"slug": "merge-requires-admin"},
        {"slug": "invented-slug", "relation": "contradicts"},
        {"slug": " gate-merge-on-checks ", "relation": "Contradicts"},
        {"slug": "gate-merge-on-checks", "relation": "contradicts"},
        {"slug": "commit-messages-in-english", "relation": "unrelated"},
        {"slug": "merge-requires-admin", "relation": "contradicts"},
    ]}
    cands = _cands("merge-requires-admin", "gate-merge-on-checks", "commit-messages-in-english")
    result = resolve(CONTRADICTION, cands, runner=_Recorded(reply))
    assert result.contradicts == ["gate-merge-on-checks", "merge-requires-admin"]
    assert result.link_basis == "extractor"
    assert result.raw is reply


def test_slug_outside_the_candidates_is_dropped():
    reply = '{"links": [{"slug": "not-shown", "relation": "contradicts"}]}'
    result = resolve(CONTRADICTION, _cands("merge-requires-admin"), runner=_Recorded(reply))
    assert result.contradicts == [] and result.link_basis == "none"
    assert result.raw["links"][0]["slug"] == "not-shown"


def test_injected_slug_corroborates():
    cands = _cands("merge-requires-admin", "gate-merge-on-checks")
    runner = _Recorded(RECORDED_CONTRADICTION)

    assert resolve(CONTRADICTION, cands, runner=runner,
                   injected=["merge-requires-admin"]).link_basis == "extractor+injected"
    assert resolve(CONTRADICTION, cands, runner=runner,
                   injected=["gate-merge-on-checks"]).link_basis == "extractor"

    rec = FrictionRecord(session_id="s", quote=CONTRADICTION.quote,
                         rule_text=CONTRADICTION.rule,
                         injected_in_session=["merge-requires-admin"])
    assert resolve(rec, cands, runner=runner).link_basis == "extractor+injected"
    # An explicit list wins over the record's own.
    assert resolve(rec, cands, runner=runner, injected=[]).link_basis == "extractor"


def test_injection_alone_is_not_a_link():
    result = resolve(CONTRADICTION, _cands("merge-requires-admin"),
                     runner=_Recorded('{"links": []}'),
                     injected=["merge-requires-admin"])
    assert result.contradicts == [] and result.link_basis == "none"


def test_no_candidates_or_no_quote_calls_no_model():
    runner = _Recorded(RECORDED_CONTRADICTION)
    assert resolve(CONTRADICTION, [], runner=runner).link_basis == "none"
    assert resolve(Correction(quote="  ", rule="x"), _cands("merge-requires-admin"),
                   runner=runner).link_basis == "none"
    assert runner.calls == []


def test_prompt_caps_each_body():
    long = Candidate(slug="long", name="Long", body="x" * 5000, score=1.0, rank=1)
    text = contradiction.build_contradiction_prompt("q", "r", [long])
    assert "x" * contradiction.MAX_BODY_CHARS in text
    assert "x" * (contradiction.MAX_BODY_CHARS + 1) not in text


def test_prompt_names_the_three_relations_and_the_dropped_slug_rule():
    p = contradiction.CONTRADICTION_PROMPT
    for relation in contradiction.RELATIONS:
        assert "- {} —".format(relation) in p
    assert "not in the list" in p and "discarded by the caller" in p
    assert set(contradiction.CONTRADICTION_SCHEMA["properties"]["links"]["items"]
               ["properties"]["relation"]["enum"]) == set(contradiction.RELATIONS)


def test_default_runner_is_the_extraction_print_path_with_hooks_off(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["env"] = kwargs["env"]
        seen["input"] = kwargs["input"]
        envelope = [{"type": "result", "result": RECORDED_CONTRADICTION, "usage": {}}]
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(envelope), stderr="")

    monkeypatch.setattr(llm, "_subprocess_run", fake_run)
    monkeypatch.setattr(link, "_default_model", lambda: ("claude-haiku-4-5", 60))
    result = resolve(CONTRADICTION, _cands("merge-requires-admin"))

    assert result.contradicts == ["merge-requires-admin"]
    assert "--print" in seen["argv"]
    assert seen["argv"][seen["argv"].index("--system-prompt") + 1] == contradiction.CONTRADICTION_PROMPT
    assert seen["env"]["MNEMO_HOOKS_OFF"] == "1"
    assert CONTRADICTION.quote in seen["input"]


def test_default_runner_missing_cli_degrades(monkeypatch):
    def missing(argv, **kwargs):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(llm, "_subprocess_run", missing)
    monkeypatch.setattr(link, "_default_model", lambda: ("claude-haiku-4-5", 60))
    result = resolve(CONTRADICTION, _cands("merge-requires-admin"))
    assert result.link_basis == "none"
    assert "LLMSubprocessError" in result.raw["error"]
