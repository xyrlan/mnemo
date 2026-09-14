"""``mnemo reverify`` — re-brief the sessions behind label-only ``verified``
pages and let today's evidence gate decide (#257).

Property under test: nothing is demoted blind. A page is demoted only when
its session was re-briefed with the current prompt and the regenerated
``## Corrections`` still does not carry its quote; a page whose transcript is
gone is reported and left alone.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mnemo.core import reverify as RV
from mnemo.core.extract.evidence import page_verifies
from mnemo.core.reclassify_types import split_frontmatter

SID_A = "aaaaaaaa-0000-0000-0000-000000000001"
SID_C = "cccccccc-0000-0000-0000-000000000003"
Q_KEPT = "never sum the rows, use the global total from the api response"
Q_LOST = "put the prisma queries in the data layer and never in a component"
Q_SHORT = "use yarn here"


def _briefing_rel(project: str, sid: str) -> str:
    return f"bots/{project}/briefings/sessions/{sid}.md"


def _write_briefing(vault: Path, project: str, sid: str, body: str = "# briefing\n\n## Decisions made\n- x\n") -> str:
    rel = _briefing_rel(project, sid)
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\ntype: briefing\nagent: {project}\nsession_id: {sid}\n---\n\n{body}", encoding="utf-8")
    return rel


def _page(vault: Path, filename: str, slug: str, *, quote: str, source: str, sources: list[str],
          confidence: str = "verified", type_: str = "feedback", extra: str = "") -> Path:
    d = vault / "shared" / type_
    d.mkdir(parents=True, exist_ok=True)
    src = "".join(f"  - {s}\n" for s in sources)
    p = d / f"{filename}.md"
    p.write_text(
        f"---\nname: {slug}\ndescription: rule {slug}\ntype: {type_}\n"
        f"extracted_at: 2026-08-10T10:00:00\nconfidence: {confidence}\nstability: stable\n"
        f"tags:\n  - t\nsources:\n{src}"
        f"evidence:\n  quote: '{quote}'\n  source: '{source}'\n  link: 'User said so'\n{extra}---\n"
        f"Body of {slug}.\n",
        encoding="utf-8",
    )
    return p


def _transcript(projects: Path, sid: str) -> Path:
    d = projects / "-nowhere-alpha"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.jsonl"
    p.write_text(json.dumps({"type": "user", "timestamp": "2026-08-10T10:00:00.000Z", "cwd": "/nowhere/alpha",
                             "message": {"role": "user", "content": Q_KEPT}}) + "\n", encoding="utf-8")
    return p


@pytest.fixture
def env(tmp_path: Path):
    vault = tmp_path / "vault"
    (vault / "shared").mkdir(parents=True)
    rel_a = _write_briefing(vault, "alpha", SID_A)  # no ## Corrections: the reclassify era
    rel_c = _write_briefing(vault, "alpha", SID_C)
    prose_a = f"briefing: {rel_a} — user turns, turn 3"
    # Two label-only pages from the same session: one the re-brief will back, one it will not.
    _page(vault, "kept-file", "kept-rule", quote=Q_KEPT, source=prose_a, sources=[rel_a])
    _page(vault, "lost-file", "lost-rule", quote=Q_LOST, source=prose_a, sources=[rel_a])
    # Too short for any gate (#119), same session.
    _page(vault, "short-file", "short-rule", quote=Q_SHORT, source=prose_a, sources=[rel_a])
    # Its transcript is gone.
    _page(vault, "gone-file", "gone-rule", quote=Q_LOST, source=f"briefing: {rel_c} — user turns, turn 2", sources=[rel_c])
    # Already gate-verified: bare source, quote in Corrections. Not a candidate.
    rel_g = _write_briefing(vault, "alpha", "gggggggg-0000-0000-0000-000000000007",
                            body=f'# briefing\n\n## Corrections\n- "{Q_KEPT}" → use the global total\n')
    _page(vault, "gate-file", "gate-rule", quote=Q_KEPT, source=rel_g, sources=[rel_g])
    # Not verified at all. Not a candidate.
    _page(vault, "inf-file", "inferred-rule", quote=Q_KEPT, source=prose_a, sources=[rel_a], confidence="inferred")
    projects = tmp_path / "projects"
    _transcript(projects, SID_A)
    return vault, projects


class FakeBriefer:
    """Stands in for ``generate_session_briefing``: writes a briefing whose
    ``## Corrections`` carries *items* into the scratch root."""

    def __init__(self, items: list[str]):
        self.items = items
        self.calls: list[tuple[str, str]] = []

    def __call__(self, jsonl: Path, agent: str, session_id: str, out_root: Path) -> Path:
        self.calls.append((agent, session_id))
        out = out_root / _briefing_rel(agent, session_id)
        out.parent.mkdir(parents=True, exist_ok=True)
        section = "## Corrections\n" + "".join(f'- "{q}" → rule for {i}\n' for i, q in enumerate(self.items))
        out.write_text(f"---\ntype: briefing\nagent: {agent}\nsession_id: {session_id}\n---\n\n# briefing\n\n{section}",
                       encoding="utf-8")
        return out


# --- candidates ----------------------------------------------------------------

def test_candidates_are_the_label_only_verified_pages(env):
    vault, _ = env
    cands = {c.slug: c for c in RV.candidates(vault)}
    assert set(cands) == {"kept-rule", "lost-rule", "short-rule", "gone-rule"}
    c = cands["kept-rule"]
    assert c.path == "shared/feedback/kept-file.md"
    assert c.briefing_rel == _briefing_rel("alpha", SID_A)
    assert c.agent == "alpha" and c.session_id == SID_A
    assert c.quote == Q_KEPT


# --- the dry run ---------------------------------------------------------------

def test_run_buckets_every_page_and_briefs_each_session_once(env):
    vault, projects = env
    briefer = FakeBriefer([Q_KEPT])
    scratch = vault / ".mnemo" / "reverify"

    report = RV.run(vault, scratch=scratch, projects_root=projects, briefer=briefer)

    by_slug = {o.slug: o for o in report.outcomes}
    assert by_slug["kept-rule"].status == RV.VERIFIED
    assert by_slug["lost-rule"].status == RV.FAILS
    assert by_slug["short-rule"].status == RV.QUOTE_TOO_SHORT
    assert by_slug["gone-rule"].status == RV.NO_TRANSCRIPT
    assert briefer.calls == [("alpha", SID_A)], "one call per session, shared by its pages"
    assert report.llm_calls == 1
    # The regenerated briefing is kept for inspection, outside the vault's bots/ tree.
    assert (scratch / "briefings" / _briefing_rel("alpha", SID_A)).is_file()
    # Nothing in the vault moved.
    assert (vault / "shared" / "feedback" / "kept-file.md").is_file()
    assert (vault / "shared" / "feedback" / "lost-file.md").is_file()
    assert "## Corrections" not in (vault / _briefing_rel("alpha", SID_A)).read_text(encoding="utf-8")


def test_run_does_not_call_the_llm_for_a_session_with_no_transcript(env):
    vault, projects = env
    (projects / "-nowhere-alpha" / f"{SID_A}.jsonl").unlink()
    briefer = FakeBriefer([Q_KEPT])

    report = RV.run(vault, scratch=vault / ".mnemo" / "reverify", projects_root=projects, briefer=briefer)

    assert briefer.calls == []
    assert {o.status for o in report.outcomes} == {RV.NO_TRANSCRIPT}


def test_run_counts_are_the_report(env):
    vault, projects = env
    report = RV.run(vault, scratch=vault / ".mnemo" / "reverify", projects_root=projects,
                    briefer=FakeBriefer([Q_KEPT]))
    assert report.counts() == {RV.VERIFIED: 1, RV.FAILS: 1, RV.QUOTE_TOO_SHORT: 1, RV.NO_TRANSCRIPT: 1}
    text = RV.format_report(report)
    assert re.search(r"verified now\s+1", text)
    assert re.search(r"still fails\s+1", text)
    assert re.search(r"quote too short\s+1", text)
    assert re.search(r"no transcript\s+1", text)
    assert "kept-rule" in text and "gone-rule" in text


# --- apply -----------------------------------------------------------------------

def test_apply_keeps_the_verified_page_so_todays_gate_passes_it(env):
    vault, projects = env
    scratch = vault / ".mnemo" / "reverify"
    report = RV.run(vault, scratch=scratch, projects_root=projects, briefer=FakeBriefer([Q_KEPT]))

    result = RV.apply(vault, report, scratch=scratch)

    page = vault / "shared" / "feedback" / "kept-file.md"
    text = page.read_text(encoding="utf-8")
    fm, _ = split_frontmatter(text)
    assert fm["confidence"] == "verified"
    assert fm["evidence"]["source"] == _briefing_rel("alpha", SID_A), "bare path, so the gate can read it"
    assert fm["evidence"]["quote"] == Q_KEPT
    assert text.count("\nevidence:\n") == 1, "re-keeping must not duplicate the evidence block"
    assert page_verifies(fm["evidence"], fm["sources"], vault) is True
    # The briefing the gate reads is the regenerated one now, and the old bytes are archived.
    assert "## Corrections" in (vault / _briefing_rel("alpha", SID_A)).read_text(encoding="utf-8")
    archived = result.archive_dir / "originals" / "briefings" / _briefing_rel("alpha", SID_A)
    assert archived.is_file() and "## Corrections" not in archived.read_text(encoding="utf-8")
    assert result.kept == 1


def test_apply_demotes_only_what_was_rebriefed_and_still_fails(env):
    vault, projects = env
    scratch = vault / ".mnemo" / "reverify"
    report = RV.run(vault, scratch=scratch, projects_root=projects, briefer=FakeBriefer([Q_KEPT]))

    result = RV.apply(vault, report, scratch=scratch)

    assert result.demoted == 2  # lost-rule and short-rule: re-briefed, still unbacked
    for slug in ("lost-rule", "short-rule"):
        moved = vault / "shared" / "reference" / f"{slug}.md"
        assert moved.is_file()
        fm, _ = split_frontmatter(moved.read_text(encoding="utf-8"))
        assert fm["type"] == "reference" and fm["confidence"] == "inferred"
        assert fm["demoted_from"] == "feedback"
    assert not (vault / "shared" / "feedback" / "lost-file.md").exists()
    # No transcript → not re-briefed → untouched, still verified.
    gone = vault / "shared" / "feedback" / "gone-file.md"
    fm, _ = split_frontmatter(gone.read_text(encoding="utf-8"))
    assert fm["confidence"] == "verified"
    # A run id the user can hand to `mnemo reclassify --undo`.
    assert (vault / "shared" / "_archive" / f"reclassify-{report.run_id}" / "manifest.json").is_file()


def _snapshot(vault: Path) -> dict:
    return {p.relative_to(vault).as_posix(): p.read_bytes()
            for sub in ("shared", "bots") for p in (vault / sub).rglob("*.md") if "_archive" not in p.parts}


def test_apply_is_undone_byte_for_byte_including_the_briefing_swap(env):
    vault, projects = env
    scratch = vault / ".mnemo" / "reverify"
    before = _snapshot(vault)
    report = RV.run(vault, scratch=scratch, projects_root=projects, briefer=FakeBriefer([Q_KEPT]))
    RV.apply(vault, report, scratch=scratch)
    assert _snapshot(vault) != before

    assert RV.undo(vault, report.run_id) >= 4  # 3 rule files + 1 briefing
    assert _snapshot(vault) == before
    assert RV.undo(vault, "no-such-run") == 0


# --- saved plan round trip ---------------------------------------------------------

def test_report_round_trips_through_json(env, tmp_path):
    vault, projects = env
    report = RV.run(vault, scratch=vault / ".mnemo" / "reverify", projects_root=projects,
                    briefer=FakeBriefer([Q_KEPT]))
    path = tmp_path / "plan.json"
    RV.save_report(report, path)
    loaded = RV.load_report(path)
    assert loaded.run_id == report.run_id
    assert [(o.slug, o.status) for o in loaded.outcomes] == [(o.slug, o.status) for o in report.outcomes]


# --- the re-brief picks a shorter span of the same turn -----------------------------

def test_a_shorter_span_of_the_pages_quote_verifies_and_apply_narrows_the_quote(env):
    """The regenerated briefing quotes the same user turn with tighter
    boundaries. That is the current prompt naming the correction; the page's
    quote is narrowed to the briefing's so ``page_verifies`` holds tomorrow."""
    vault, projects = env
    shorter = "never sum the rows, use the global total"  # inside Q_KEPT, ≥5 content words
    scratch = vault / ".mnemo" / "reverify"
    report = RV.run(vault, scratch=scratch, projects_root=projects, briefer=FakeBriefer([shorter]))
    by_slug = {o.slug: o for o in report.outcomes}
    assert by_slug["kept-rule"].status == RV.VERIFIED
    assert by_slug["kept-rule"].quote == shorter

    RV.apply(vault, report, scratch=scratch)
    fm, _ = split_frontmatter((vault / "shared" / "feedback" / "kept-file.md").read_text(encoding="utf-8"))
    assert fm["evidence"]["quote"] == shorter
    assert page_verifies(fm["evidence"], fm["sources"], vault) is True


def test_a_shorter_span_that_is_itself_too_short_does_not_verify(env):
    vault, projects = env
    report = RV.run(vault, scratch=vault / ".mnemo" / "reverify", projects_root=projects,
                    briefer=FakeBriefer(["never sum the rows"]))
    assert {o.slug: o.status for o in report.outcomes}["kept-rule"] == RV.FAILS


# --- rerunning reuses the scratch briefings unless told otherwise -------------------

def test_default_briefer_reuses_an_unchanged_scratch_briefing_unless_fresh(monkeypatch, tmp_path):
    from mnemo.core import briefing as B

    seen: list[dict] = []

    def fake_generate(jsonl, agent, cfg, **kw):
        seen.append({"vaultRoot": cfg.get("vaultRoot"), **kw})
        out = Path(cfg["vaultRoot"]) / "bots" / agent / "briefings" / "sessions" / f"{jsonl.stem}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("# b\n", encoding="utf-8")
        return out

    monkeypatch.setattr(B, "generate_session_briefing", fake_generate)
    jsonl = tmp_path / f"{SID_A}.jsonl"
    jsonl.write_text("{}\n", encoding="utf-8")
    scratch = tmp_path / "scratch"

    RV.default_briefer({"vaultRoot": "/real/vault"})(jsonl, "alpha", SID_A, scratch)
    RV.default_briefer({"vaultRoot": "/real/vault"}, fresh=True)(jsonl, "alpha", SID_A, scratch)

    assert seen[0]["vaultRoot"] == str(scratch) and seen[0]["reuse_unchanged"] is True
    assert seen[1]["reuse_unchanged"] is False
    assert all(s["min_mutations"] == 0 for s in seen)
