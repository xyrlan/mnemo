"""The reference gate (#417): an inferred reference page goes live on a verdict.

Every end-to-end test here asserts both halves — where the page landed *and*
where it did not — because a gate that writes the page to both places passes
either assertion alone.
"""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core import llm as llm_mod
from mnemo.core.extract import reference_gate as rg
from mnemo.core.extract import run_extraction
from mnemo.core.extract.inbox.types import ExtractedPage


def _page(slug="p", type_="reference", **kw) -> ExtractedPage:
    return ExtractedPage(
        slug=slug, type=type_, name=kw.pop("name", "Name"), description="d",
        body=kw.pop("body", "Body."), source_files=["bots/a/memory/x.md"],
        source_hash="h", **kw,
    )


# --------------------------------------------------------------------------
# parsing the judge's answer
# --------------------------------------------------------------------------

def test_parse_verdicts_reads_each_entry_by_its_number():
    text = json.dumps({"verdicts": [{"i": 2, "cat": "g"}, {"i": 1, "cat": "S"}]})
    assert rg.parse_verdicts(text, 2) == ["S", "G"]


def test_parse_verdicts_tolerates_a_fence_around_the_json():
    text = "```json\n" + json.dumps({"verdicts": [{"i": 1, "cat": "T"}]}) + "\n```"
    assert rg.parse_verdicts(text, 1) == ["T"]


def test_parse_verdicts_gives_no_answer_for_anything_it_cannot_trust():
    text = json.dumps({"verdicts": [
        {"i": 1, "cat": "X"},      # not a category
        {"i": 5, "cat": "S"},      # out of range
        {"i": True, "cat": "S"},   # a bool is not an index
        "S",                       # not an object
    ]})
    assert rg.parse_verdicts(text, 2) == [None, None]
    assert rg.parse_verdicts("no json here", 2) == [None, None]
    assert rg.parse_verdicts('{"pages": []}', 1) == [None]
    assert rg.parse_verdicts("{not json}", 1) == [None]


def test_view_is_name_and_body_on_one_line_cut_at_the_audit_length():
    assert rg.view("Title.", "line one\n\nline  two") == "Title. line one line two"
    assert len(rg.view("n", "x " * 5000)) == rg.VIEW_CHARS


# --------------------------------------------------------------------------
# which pages are judged, and what a failure means
# --------------------------------------------------------------------------

def test_inferred_reference_pages_and_demotions_are_judged_in_one_call():
    pages = [
        _page("ref"),
        _page("fb", type_="feedback", confidence="verified"),
        _page("demoted", unverified_feedback=True),
        _page("user", type_="user"),
    ]
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return json.dumps({"verdicts": [{"i": 1, "cat": "S"}, {"i": 2, "cat": "G"}]})

    out = rg.judge_pages(pages, ask)
    assert [p.judged for p in out] == ["S", None, "G", None]
    assert len(prompts) == 1 and "[2]" in prompts[0] and "[3]" not in prompts[0]


def test_a_demotion_is_held_but_not_by_this_gate(tmp_path):
    """Routing reads ``unverified_feedback`` first, so a T/S demotion stages
    anyway; ``held`` still answers for the verdict, and the apply loop is what
    keeps demotions out of ``reference_held``."""
    from mnemo.core.extract.inbox.paths import _target_path_for_page

    kept = _page(judged="T", unverified_feedback=True)
    assert rg.cleared(kept) and not rg.held(kept, tmp_path)
    assert "_inbox" in _target_path_for_page(kept, tmp_path).parts


def test_no_call_when_nothing_needs_judging():
    def ask(prompt):  # pragma: no cover - must not run
        raise AssertionError("judge called with nothing to judge")

    pages = [_page("fb", type_="feedback", confidence="verified")]
    assert rg.judge_pages(pages, ask) is pages


def test_a_judge_that_fails_holds_the_page_back():
    def ask(prompt):
        raise llm_mod.LLMSubprocessError("boom")

    (out,) = rg.judge_pages([_page()], ask)
    assert out.judged == ""
    assert not rg.cleared(out)


def test_judging_does_not_mutate_the_callers_page():
    page = _page()
    rg.judge_pages([page], lambda _: json.dumps({"verdicts": [{"i": 1, "cat": "G"}]}))
    assert page.judged is None


# --------------------------------------------------------------------------
# held: what stages this run
# --------------------------------------------------------------------------

def test_held_follows_the_verdict_for_a_new_page(tmp_path):
    assert not rg.held(_page(), tmp_path)                 # never judged
    assert not rg.held(_page(judged="S"), tmp_path)
    assert not rg.held(_page(judged="T"), tmp_path)
    assert rg.held(_page(judged="G"), tmp_path)
    assert rg.held(_page(judged="N"), tmp_path)
    assert rg.held(_page(judged=""), tmp_path)            # no answer


def test_a_live_page_is_never_held(tmp_path):
    live = tmp_path / "shared" / "reference" / "p.md"
    live.parent.mkdir(parents=True)
    live.write_text("x", encoding="utf-8")
    assert not rg.held(_page(judged="G"), tmp_path)


def test_a_staged_page_stays_staged_whatever_the_verdict(tmp_path):
    staged = tmp_path / "shared" / "_inbox" / "reference" / "p.md"
    staged.parent.mkdir(parents=True)
    staged.write_text("x", encoding="utf-8")
    assert rg.held(_page(judged="S"), tmp_path)


# --------------------------------------------------------------------------
# end to end through run_extraction
# --------------------------------------------------------------------------

def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "shared").mkdir(parents=True)
    d = root / "bots" / "agent" / "memory"
    d.mkdir(parents=True)
    for stem in ("one", "two"):
        (d / f"{stem}.md").write_text(
            f"---\nname: {stem}\ntype: reference\ndescription: d\n---\n\nbody {stem}\n",
            encoding="utf-8",
        )
    return root


def _cfg(root: Path, **gate) -> dict:
    return {"vaultRoot": str(root), "extraction": {
        "model": "m", "chunkSize": 10, "subprocessTimeout": 60,
        "referenceGate": {"enabled": True, "model": "judge-model", **gate},
    }}


def _resp(payload) -> llm_mod.LLMResponse:
    return llm_mod.LLMResponse(
        text=json.dumps(payload), total_cost_usd=0.0,
        input_tokens=1, output_tokens=1, api_key_source="none", raw={},
    )


def _stub(monkeypatch, pages, verdicts):
    calls = []

    def call(prompt, *, system, model, timeout):
        calls.append((system, model))
        if system == rg.SYSTEM_PROMPT:
            return _resp({"verdicts": [
                {"i": i, "cat": c} for i, c in enumerate(verdicts, 1)
            ]})
        return _resp({"pages": pages})

    monkeypatch.setattr(llm_mod, "call", call)
    return calls


_PAGES = [
    {"slug": "dependency-injection", "type": "reference", "name": "DI is good",
     "description": "d", "body": "Inject dependencies.",
     "source_files": ["bots/agent/memory/one.md"]},
    {"slug": "wda-exclude", "type": "reference", "name": "WDA flag",
     "description": "d", "body": "WDA_EXCLUDEFROMCAPTURE blocks BitBlt.",
     "source_files": ["bots/agent/memory/two.md"]},
]


def test_a_generic_page_stages_and_a_specific_one_goes_live(tmp_path, monkeypatch):
    root = _vault(tmp_path)
    calls = _stub(monkeypatch, _PAGES, ["G", "T"])

    summary = run_extraction(_cfg(root))

    live, staged = root / "shared" / "reference", root / "shared" / "_inbox" / "reference"
    assert (live / "wda-exclude.md").exists()
    assert not (staged / "wda-exclude.md").exists()
    assert (staged / "dependency-injection.md").exists()
    assert not (live / "dependency-injection.md").exists()
    assert summary.reference_held == 1
    # The judge ran once, on its own model, after the consolidation call.
    assert (rg.SYSTEM_PROMPT, "judge-model") in calls
    assert sum(1 for s, _ in calls if s == rg.SYSTEM_PROMPT) == 1
    # The learned ledger announces only what went live.
    ledger = (root / ".mnemo" / "learned.jsonl").read_text(encoding="utf-8")
    assert "wda-exclude" in ledger and "dependency-injection" not in ledger


def test_gate_off_restores_the_old_door(tmp_path, monkeypatch):
    root = _vault(tmp_path)
    calls = _stub(monkeypatch, _PAGES, ["G", "G"])

    summary = run_extraction(_cfg(root, enabled=False))

    live = root / "shared" / "reference"
    assert (live / "dependency-injection.md").exists()
    assert (live / "wda-exclude.md").exists()
    assert summary.reference_held == 0
    assert all(s != rg.SYSTEM_PROMPT for s, _ in calls)


def test_a_judge_answer_without_verdicts_holds_every_page(tmp_path, monkeypatch):
    root = _vault(tmp_path)
    monkeypatch.setattr(llm_mod, "call", lambda *a, **k: _resp({"pages": _PAGES}))

    summary = run_extraction(_cfg(root))

    staged = root / "shared" / "_inbox" / "reference"
    assert (staged / "dependency-injection.md").exists()
    assert (staged / "wda-exclude.md").exists()
    assert not (root / "shared" / "reference").exists() or not any(
        (root / "shared" / "reference").iterdir()
    )
    assert summary.reference_held == 2


def test_a_live_page_reemitted_is_reinforced_not_staged(tmp_path, monkeypatch):
    """The stock is not this gate's: a verdict never moves a live page."""
    root = _vault(tmp_path)
    _stub(monkeypatch, _PAGES, ["S", "T"])
    run_extraction(_cfg(root))
    live = root / "shared" / "reference" / "dependency-injection.md"
    assert live.exists()

    # The source changes, the page is re-emitted, and the judge now says G.
    (root / "bots" / "agent" / "memory" / "one.md").write_text(
        "---\nname: one\ntype: reference\ndescription: d\n---\n\nbody changed\n",
        encoding="utf-8",
    )
    changed = [dict(_PAGES[0], body="Inject dependencies, always.")]
    _stub(monkeypatch, changed, ["G"])
    summary = run_extraction(_cfg(root))

    assert live.exists()
    assert "always" in live.read_text(encoding="utf-8")
    assert not (root / "shared" / "_inbox" / "reference" / "dependency-injection.md").exists()
    assert summary.reference_held == 0


_DEMOTED = {"slug": "write-clean-code", "type": "feedback", "name": "Write clean code",
            "description": "d", "body": "Keep functions small.",
            "source_files": ["bots/agent/memory/one.md"]}  # no quote: demoted


def test_a_demotion_is_judged_staged_and_stamped(tmp_path, monkeypatch):
    """#432: the demotion rides the chunk's one judge call, stages as before,
    carries its label, and is not counted as a page the judge held."""
    from mnemo.core import inbox as I
    from mnemo.core.extract.scanner import parse_frontmatter

    root = _vault(tmp_path)
    calls = _stub(monkeypatch, [_DEMOTED, _PAGES[1]], ["G", "T"])

    summary = run_extraction(_cfg(root))

    staged = root / "shared" / "_inbox" / "reference" / "write-clean-code.md"
    fm, _ = parse_frontmatter(staged.read_text(encoding="utf-8"))
    assert fm["demoted_from"] == "feedback"
    assert fm["reference_gate"] == "generic"
    assert (root / "shared" / "reference" / "wda-exclude.md").exists()
    assert sum(1 for s, _ in calls if s == rg.SYSTEM_PROMPT) == 1
    assert summary.demoted_unverified == 1
    assert summary.reference_held == 0
    assert [p.key for p in I.staged_pages(root) if p.gate_held] == ["reference/write-clean-code"]


def test_a_demotion_the_judge_would_keep_still_stages_and_says_so(tmp_path, monkeypatch):
    from mnemo.core.extract.scanner import parse_frontmatter

    root = _vault(tmp_path)
    calls = _stub(monkeypatch, [_DEMOTED], ["S"])

    summary = run_extraction(_cfg(root))

    staged = root / "shared" / "_inbox" / "reference" / "write-clean-code.md"
    assert parse_frontmatter(staged.read_text(encoding="utf-8"))[0]["reference_gate"] == "system"
    assert not (root / "shared" / "reference" / "write-clean-code.md").exists()
    assert not (root / "shared" / "feedback" / "write-clean-code.md").exists()
    # A chunk of demotions alone costs one judge call.
    assert sum(1 for s, _ in calls if s == rg.SYSTEM_PROMPT) == 1
    assert summary.reference_held == 0


def test_an_invented_type_cannot_mint_a_shared_directory(tmp_path, monkeypatch):
    root = _vault(tmp_path)
    invented = [dict(_PAGES[1], type="measurement-before-design")]
    _stub(monkeypatch, invented, ["T"])

    run_extraction(_cfg(root))

    assert not (root / "shared" / "measurement-before-design").exists()
    assert not (root / "shared" / "_inbox" / "measurement-before-design").exists()
    # Falls back to the kind the prompt asked for, and meets its gate.
    assert (root / "shared" / "reference" / "wda-exclude.md").exists()
    state = json.loads(
        (root / ".mnemo" / "extraction-state.json").read_text(encoding="utf-8")
    )
    assert not any(k.startswith("measurement-before-design/") for k in state["entries"])


def test_two_projects_of_sources_do_not_promote_an_uncleared_page(tmp_path):
    from mnemo.core.extract.inbox.apply import _is_universal_promotion

    target = tmp_path / "shared" / "_inbox" / "reference" / "p.md"
    sources = ["bots/alpha/memory/x.md", "bots/beta/memory/y.md"]
    generic = _page(judged="G")
    generic.source_files = sources
    specific = _page(judged="S")
    specific.source_files = sources

    assert _is_universal_promotion(specific, None, target, False)
    assert not _is_universal_promotion(generic, None, target, False)
