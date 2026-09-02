"""core/reclassify: verdict validation, apply with manifest, byte-exact undo."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core import reclassify as R

BRIEF = """---
type: briefing
agent: proj
session_id: s1
---
## Decisions made
- used yarn

## Corrections
- "use yarn not npm in this repo" → Use yarn
"""


def _rule(root: Path, slug: str, name: str, body: str = "Body.\n\n**Why:** w\n\n**How to apply:** h"):
    d = root / "shared" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{slug}.md"
    p.write_text(f"---\nslug: {slug}\nname: {name}\ndescription: {name}\ntype: feedback\nstability: stable\n"
                 f"sources:\n  - bots/proj/briefings/sessions/s1.md\ntags:\n  - auto-promoted\n  - x\n---\n{body}\n", encoding="utf-8")
    return p


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    b = root / "bots" / "proj" / "briefings" / "sessions" / "s1.md"
    b.parent.mkdir(parents=True)
    b.write_text(BRIEF, encoding="utf-8")
    (root / ".mnemo").mkdir()
    (root / ".mnemo" / "extraction-state.json").write_text(json.dumps({
        "schema_version": 3, "last_run": None, "entries": {
            "feedback/use-yarn": {"source_files": ["bots/proj/briefings/sessions/s1.md"], "source_hash": "a",
                                   "written_hash": "b", "written_at": "r", "status": "auto_promoted"}}}), encoding="utf-8")
    return root


def test_collect_rules_reads_live_feedback_only(vault):
    _rule(vault, "use-yarn", "Use yarn")
    (vault / "shared" / "feedback" / "x.proposed.md").write_text("---\nname: p\n---\n", encoding="utf-8")
    assert [r.slug for r in R.collect_rules(vault)] == ["use-yarn"]


def test_validate_downgrades_bad_merge_and_unverifiable_keep(vault, tmp_path):
    _rule(vault, "use-yarn", "Use yarn")
    _rule(vault, "generic", "Generic tip")
    rules = {r.slug: r for r in R.collect_rules(vault)}
    verdicts = [
        R.Verdict(slug="use-yarn", verdict="keep", quote="use yarn not npm in this repo",
                  source="bots/proj/briefings/sessions/s1.md"),
        R.Verdict(slug="generic", verdict="keep", quote="words nobody typed here"),
        R.Verdict(slug="generic", verdict="merge", target="does-not-exist"),
        R.Verdict(slug="generic", verdict="banana"),
    ]
    out = R.validate(verdicts, rules, vault, projects_root=tmp_path / "none")
    assert [(v.slug, v.verdict) for v in out] == [
        ("use-yarn", "keep"), ("generic", "demote"), ("generic", "demote"), ("generic", "archive")]


def test_apply_moves_files_writes_manifest_and_undo_restores_bytes(vault, tmp_path):
    keep = _rule(vault, "use-yarn", "Use yarn")
    demote = _rule(vault, "project-fact", "Deploy needs VPN")
    dup = _rule(vault, "use-yarn-dup", "Use yarn (dup)")
    junk = _rule(vault, "generic", "Generic tip")
    originals = {p: p.read_bytes() for p in (keep, demote, dup, junk)}
    state_before = (vault / ".mnemo" / "extraction-state.json").read_bytes()

    plan = R.Plan(run_id="20260901T000000", llm_calls=0, verdicts=[
        R.Verdict(slug="use-yarn", verdict="keep", quote="use yarn not npm in this repo",
                  source="bots/proj/briefings/sessions/s1.md"),
        R.Verdict(slug="project-fact", verdict="demote"),
        R.Verdict(slug="use-yarn-dup", verdict="merge", target="use-yarn"),
        R.Verdict(slug="generic", verdict="archive"),
    ])
    report = R.apply(vault, plan, rebuild_indexes=False)

    kept_text = keep.read_text(encoding="utf-8")
    assert "confidence: verified" in kept_text and "use yarn not npm in this repo" in kept_text
    assert not demote.exists()
    demoted = vault / "shared" / "reference" / "project-fact.md"
    assert demoted.exists() and "type: reference" in demoted.read_text(encoding="utf-8") and "demoted_from: feedback" in demoted.read_text(encoding="utf-8")
    assert not dup.exists() and not junk.exists()
    arch = vault / "shared" / "_archive" / "reclassify-20260901T000000"
    assert (arch / "merged" / "use-yarn-dup.md").exists() and (arch / "archived" / "generic.md").exists()
    manifest = json.loads((arch / "manifest.json").read_text(encoding="utf-8"))
    assert {m["verdict"] for m in manifest["moves"]} == {"keep", "demote", "merge", "archive"}
    assert report.kept == 1 and report.demoted == 1 and report.merged == 1 and report.archived == 1
    state = json.loads((vault / ".mnemo" / "extraction-state.json").read_text(encoding="utf-8"))
    assert "reference/project-fact" in state["entries"]
    assert state["entries"]["feedback/use-yarn-dup"]["status"] == "dismissed"

    restored = R.undo(vault, "20260901T000000")
    assert restored >= 4
    for p, data in originals.items():
        assert p.read_bytes() == data
    assert not demoted.exists()
    assert (vault / ".mnemo" / "extraction-state.json").read_bytes() == state_before


def test_plan_batches_and_parses_llm_verdicts(vault, tmp_path):
    for i in range(12):
        _rule(vault, f"r{i}", f"Rule {i}")
    calls = []

    def fake_call(prompt, *, system, model, timeout):
        calls.append(prompt)
        slugs = [l.split(":", 1)[0].strip("- ") for l in prompt.splitlines() if l.startswith("- r")]
        payload = {"verdicts": [{"slug": s, "verdict": "archive", "reason": "generic"} for s in slugs]}
        from mnemo.core.llm import LLMResponse
        return LLMResponse(text=json.dumps(payload), total_cost_usd=0.0, input_tokens=1,
                           output_tokens=1, api_key_source="none", raw={})

    plan = R.plan(vault, model="m", timeout=5, batch_size=10, projects_root=tmp_path / "none", call=fake_call)
    assert plan.llm_calls == 2 and len(plan.verdicts) == 12
    assert all(v.verdict == "archive" for v in plan.verdicts)
    R.save_plan(vault, plan)
    assert R.load_plan(vault).run_id == plan.run_id


def test_transcript_turns_found_by_session_id(vault, tmp_path):
    projects = tmp_path / "projects" / "-Users-x-proj"
    projects.mkdir(parents=True)
    (projects / "s1.jsonl").write_text(json.dumps(
        {"type": "user", "message": {"role": "user", "content": "use yarn not npm in this repo"}}) + "\n", encoding="utf-8")
    turns = R.transcript_turns(vault, "bots/proj/briefings/sessions/s1.md", projects_root=tmp_path / "projects")
    assert turns == ["use yarn not npm in this repo"]


def test_apply_resolves_files_by_frontmatter_slug_not_filename(vault):
    """97% of the real vault has ``slug != path.stem`` — apply must not guess."""
    d = vault / "shared" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    odd = d / "Some File.md"
    odd.write_text("---\nname: Use yarn\ndescription: Use yarn\ntype: feedback\n"
                   "sources:\n  - bots/proj/briefings/sessions/s1.md\n---\nBody.\n", encoding="utf-8")
    assert [r.slug for r in R.collect_rules(vault)] == ["use-yarn"]

    # path=None forces the slug -> path fallback map.
    plan = R.Plan(run_id="20260902T000000", llm_calls=0, verdicts=[
        R.Verdict(slug="use-yarn", verdict="demote"),
        R.Verdict(slug="ghost-rule", verdict="archive"),
    ])
    report = R.apply(vault, plan, rebuild_indexes=False)

    assert report.demoted == 1 and report.archived == 0
    assert not odd.exists()
    assert (vault / "shared" / "reference" / "use-yarn.md").exists()
    assert report.skipped == [{"slug": "ghost-rule", "reason": "rule file not found"}]
    arch = vault / "shared" / "_archive" / "reclassify-20260902T000000"
    manifest = json.loads((arch / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["skipped"] == [{"slug": "ghost-rule", "reason": "rule file not found"}]

    R.undo(vault, "20260902T000000")
    assert odd.exists() and "name: Use yarn" in odd.read_text(encoding="utf-8")


def test_plan_records_vault_relative_path_for_each_verdict(vault, tmp_path):
    d = vault / "shared" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    (d / "Some File.md").write_text("---\nname: Use yarn\ntype: feedback\n---\nBody.\n", encoding="utf-8")

    def fake_call(prompt, *, system, model, timeout):
        from mnemo.core.llm import LLMResponse
        payload = {"verdicts": [{"slug": "use-yarn", "verdict": "archive"}]}
        return LLMResponse(text=json.dumps(payload), total_cost_usd=0.0, input_tokens=1,
                           output_tokens=1, api_key_source="none", raw={})

    plan = R.plan(vault, model="m", timeout=5, projects_root=tmp_path / "none", call=fake_call)
    assert [v.path for v in plan.verdicts] == ["shared/feedback/Some File.md"]

    R.save_plan(vault, plan)
    assert R.load_plan(vault).verdicts[0].path == "shared/feedback/Some File.md"


def test_apply_refuses_to_run_twice_with_same_run_id(vault):
    junk = _rule(vault, "generic", "Generic tip")
    original = junk.read_bytes()
    plan = R.Plan(run_id="20260902T111111", llm_calls=0,
                  verdicts=[R.Verdict(slug="generic", verdict="archive")])
    assert R.apply(vault, plan, rebuild_indexes=False).archived == 1

    with pytest.raises(RuntimeError, match="already applied"):
        R.apply(vault, plan, rebuild_indexes=False)

    assert R.undo(vault, "20260902T111111") >= 1
    assert junk.read_bytes() == original


def test_load_plan_rejects_path_traversal(vault):
    plan_path = vault / ".mnemo" / R.PLAN_FILENAME
    plan_path.parent.mkdir(parents=True, exist_ok=True)

    plan_path.write_text(json.dumps({"run_id": "r", "llm_calls": 0, "verdicts": [
        {"slug": "../../evil", "verdict": "archive"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="illegal slug"):
        R.load_plan(vault)

    plan_path.write_text(json.dumps({"run_id": "r", "llm_calls": 0, "verdicts": [
        {"slug": "ok", "verdict": "merge", "target": "../evil"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="illegal target"):
        R.load_plan(vault)

    plan_path.write_text(json.dumps({"run_id": "r", "llm_calls": 0, "verdicts": [
        {"slug": "ok", "verdict": "archive", "path": "shared/../../etc/passwd"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="illegal path"):
        R.load_plan(vault)

    plan_path.write_text(json.dumps({"run_id": "r", "llm_calls": 0, "verdicts": [
        {"slug": "ok", "verdict": "archive", "path": "/etc/passwd"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="illegal path"):
        R.load_plan(vault)


def test_known_slugs_are_bounded(vault, tmp_path):
    for i in range(300):
        _rule(vault, f"cache-rule-{i}", f"Cache rule {i}")
    rules = R.collect_rules(vault)
    batch = rules[:10]
    known = R.known_slugs_for(batch, rules)
    assert len(known) <= 10 + 150
    assert set(r.slug for r in batch) <= set(known)

    prompt = R.build_prompt(batch, {}, known)
    listed = prompt.rsplit("Known slugs: ", 1)[1].split(", ")
    assert len(listed) <= 160


def test_demoted_entry_keeps_origin_backfill_and_keep_creates_missing_entry(vault):
    _rule(vault, "use-yarn", "Use yarn")
    _rule(vault, "project-fact", "Deploy needs VPN")
    state_path = vault / ".mnemo" / "extraction-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    # A backfilled rule: the flag must survive the demote.
    state["entries"]["feedback/project-fact"] = {
        "source_files": [], "source_hash": "h", "written_hash": "w",
        "written_at": "r", "status": "auto_promoted", "origin_backfill": True}
    # ...and `use-yarn` has no entry at all, so `keep` must create one.
    state["entries"].pop("feedback/use-yarn", None)
    state_path.write_text(json.dumps(state), encoding="utf-8")

    plan = R.Plan(run_id="20260902T222222", llm_calls=0, verdicts=[
        R.Verdict(slug="use-yarn", verdict="keep", quote="use yarn not npm in this repo",
                  source="bots/proj/briefings/sessions/s1.md"),
        R.Verdict(slug="project-fact", verdict="demote"),
    ])
    R.apply(vault, plan, rebuild_indexes=False)

    entries = json.loads(state_path.read_text(encoding="utf-8"))["entries"]
    assert entries["reference/project-fact"]["origin_backfill"] is True
    assert entries["reference/project-fact"]["source_hash"] == "h"
    assert entries["reference/project-fact"]["written_at"].startswith("20")
    assert "T" in entries["reference/project-fact"]["last_sync"]
    kept = entries["feedback/use-yarn"]
    assert kept["written_hash"]


def test_has_transcript_flags_rules_whose_sessions_are_gone(vault, tmp_path):
    _rule(vault, "use-yarn", "Use yarn")
    rule = R.collect_rules(vault)[0]
    assert R.has_transcript(vault, rule, projects_root=tmp_path / "none") is False
    projects = tmp_path / "projects" / "-Users-x-proj"
    projects.mkdir(parents=True)
    (projects / "s1.jsonl").write_text(json.dumps(
        {"type": "user", "message": {"role": "user", "content": "use yarn not npm"}}) + "\n", encoding="utf-8")
    assert R.has_transcript(vault, rule, projects_root=tmp_path / "projects") is True


def test_undo_restores_merge_target_at_its_real_path_not_its_slug(vault):
    """Merge undo must restore the target where it lives, not at <slug>.md.

    97% of the real vault has ``filename != slug``. Rebuilding the target as
    ``shared/feedback/<slug>.md`` writes a stray file and leaves the real
    target carrying the merged-in sources forever.
    """
    d = vault / "shared" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    target = d / "Some Other Name.md"
    target.write_text(
        "---\nslug: use-yarn\nname: Use yarn\ndescription: Use yarn\ntype: feedback\n"
        "sources:\n  - bots/proj/briefings/sessions/s1.md\n---\nBody.\n",
        encoding="utf-8",
    )
    original = target.read_bytes()
    dup = _rule(vault, "use-yarn-dup", "Use yarn too")
    dup.write_text(
        dup.read_text(encoding="utf-8").replace(
            "sources:\n  - bots/proj/briefings/sessions/s1.md",
            "sources:\n  - bots/proj/briefings/sessions/s2.md",
        ),
        encoding="utf-8",
    )

    plan = R.Plan(run_id="20260902T222222", llm_calls=0, verdicts=[
        R.Verdict(slug="use-yarn-dup", verdict="merge", target="use-yarn"),
    ])
    assert R.apply(vault, plan, rebuild_indexes=False).merged == 1
    assert target.read_bytes() != original, "the merge must have touched the target"

    manifest = json.loads(
        (vault / "shared" / "_archive" / "reclassify-20260902T222222" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["moves"][0]["target_path"] == "shared/feedback/Some Other Name.md"

    R.undo(vault, "20260902T222222")
    assert target.read_bytes() == original
    assert not (d / "use-yarn.md").exists(), "undo must not write a stray slug-named file"
    assert dup.exists()
