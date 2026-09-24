"""#492: ``regen-graph-edges`` and reclassify's keep/merge advance
``written_hash`` on the page's real key, and the drift they already left is
re-baselined — while the edits a person asked for stay protected.

Fixtures are the shapes #487 found on the maintainer's vault, scrubbed: pages
rendered by the extractor (pre-#114, so no ``slug:``, sources often absolute),
then edited by the writers' own functions in the order they ran there.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.cli.commands import regen_graph_edges as regen
from mnemo.core import locks
from mnemo.core import reclassify_apply as RA
from mnemo.core.extract import machine_edits
from mnemo.core.extract.inbox.io import content_hash
from mnemo.core.extract.inbox.rendering import _render_page
from mnemo.core.extract.inbox.state_io import atomic_write_state, load_state
from mnemo.core.extract.inbox.types import ExtractedPage
from mnemo.core.extract.scanner import ExtractionState, StateEntry
from mnemo.core.migrations.slugs import _stamp
from mnemo.core.reclassify_types import Plan, Verdict

RUN = "2026-08-11T16:48:42"
S1 = "bots/proj/briefings/sessions/0f3c9a1e-5b7d-4e2a-9c61-1d2e3f4a5b6c.md"
S2 = "bots/proj/briefings/sessions/7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d.md"
S3 = "bots/other/briefings/sessions/2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e.md"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _extracted(vault: Path, state: ExtractionState, ty: str, stem: str, *,
               name: str, sources, ledger_sources=None, absolute: bool = True) -> Path:
    """A page as an extraction before #114 wrote it: no ``slug:`` line, its
    sources (and so its section) rendered with the vault's absolute path."""
    rendered = [_abs(vault, s) if absolute else s for s in sources]
    page = ExtractedPage(
        slug=stem, type=ty, name=name, description="what the maintainer measured",
        body="Measure before you design.\n\n**Why:** the numbers were wrong.",
        source_files=rendered, source_hash="sha256:src", tags=["workflow"],
    )
    text = _render_page(page, run_id=RUN, auto_promoted=True).replace(f"slug: {stem}\n", "")
    path = vault / "shared" / ty / f"{stem}.md"
    _write(path, text)
    state.entries[f"{ty}/{stem}"] = StateEntry(
        source_files=list(ledger_sources if ledger_sources is not None else sources),
        source_hash="sha256:src", written_hash=content_hash(text),
        written_at=RUN, status="auto_promoted", last_sync=RUN,
    )
    return path


def _abs(vault: Path, rel: str) -> str:
    """An absolute source as the renderer spelled it: ``str(Path)``, so with
    backslashes on Windows."""
    return str(vault.joinpath(*rel.split("/")))


def _stamp_slug(path: Path, slug: str) -> None:
    """The #114 stamp as it ran before #179: bytes only, no hash."""
    _write(path, _stamp(_text(path), slug))


def _old_regen(path: Path, vault: Path) -> None:
    """``regen-graph-edges`` before #492: its bytes, no hash moved. Written
    exactly, as on the macOS vault #487 measured: ``write_text`` would add
    CRLF on Windows, a drift of its own."""
    before = _text(path)
    after = regen.refreshed_rule(before, vault)
    assert after != before
    _write(path, after)


def _save(vault: Path, state: ExtractionState) -> None:
    atomic_write_state(state, vault / machine_edits.STATE_REL)


def _state(vault: Path) -> ExtractionState:
    return load_state(vault / machine_edits.STATE_REL)


def _empty() -> ExtractionState:
    return ExtractionState(last_run=None, entries={})


def _explain(vault: Path, state: ExtractionState, key: str, path: Path, merged=()):
    entry = state.entries[key]
    return machine_edits.explain_drift(
        _text(path), entry.written_hash, machine_edits.vault_prefixes(vault),
        entry.source_files, merged, vault)


# ---------------------------------------------------------------------------
# 1. The writers: each advances written_hash, on the page's real key
# ---------------------------------------------------------------------------


def test_regen_advances_the_hash_of_a_page_it_rewrites(tmp_vault: Path, monkeypatch, capsys):
    state = _empty()
    page = _extracted(tmp_vault, state, "reference", "measure-first",
                      name="Measure first", sources=[S1])
    _save(tmp_vault, state)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: tmp_vault)

    assert regen.cmd_regen_graph_edges(None) == 0

    after = _text(page)
    assert f"[[{S1[:-3]}]]" in after and str(tmp_vault) not in after.split("## Sources")[1]
    assert _state(tmp_vault).entries["reference/measure-first"].written_hash == content_hash(after)


def test_regen_leaves_a_person_s_page_reading_as_edited(tmp_vault: Path, monkeypatch):
    state = _empty()
    page = _extracted(tmp_vault, state, "reference", "measure-first",
                      name="Measure first", sources=[S1])
    written = state.entries["reference/measure-first"].written_hash
    _write(page, _text(page).replace("Measure before", "Always measure before"))
    _save(tmp_vault, state)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: tmp_vault)

    assert regen.cmd_regen_graph_edges(None) == 0

    assert "Always measure before" in _text(page)
    assert _state(tmp_vault).entries["reference/measure-first"].written_hash == written


def test_regen_stands_down_while_an_extraction_holds_the_vault(tmp_vault: Path, monkeypatch, capsys):
    state = _empty()
    page = _extracted(tmp_vault, state, "reference", "measure-first",
                      name="Measure first", sources=[S1])
    _save(tmp_vault, state)
    before = page.read_bytes()
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: tmp_vault)
    lock = tmp_vault / machine_edits.LOCK_REL
    lock.parent.mkdir(parents=True, exist_ok=True)

    with locks.try_lock(lock) as held:
        assert held
        assert regen.cmd_regen_graph_edges(None) == 1

    assert page.read_bytes() == before
    assert "extraction is in progress" in capsys.readouterr().err


def _keep_verdict(slug: str, path: str) -> Verdict:
    return Verdict(slug=slug, verdict="keep", quote="comeca pela higiene entao",
                   source=S1, path=path)


def test_keep_advances_the_page_s_own_key_not_its_slug(tmp_vault: Path):
    """The stem and the frontmatter slug differed for 61/61 keeps (#487)."""
    state = _empty()
    page = _extracted(tmp_vault, state, "feedback", "vault-cleanup",
                      name="Deduplicate before ranking", sources=[S1])
    _stamp_slug(page, "deduplicate-before-ranking")
    state.entries["feedback/vault-cleanup"].written_hash = content_hash(page)
    _save(tmp_vault, state)

    RA.apply(tmp_vault, Plan(run_id="20260902T152055", llm_calls=0, verdicts=[
        _keep_verdict("deduplicate-before-ranking", "shared/feedback/vault-cleanup.md")]),
        rebuild_indexes=False)

    entries = _state(tmp_vault).entries
    assert "confidence: verified" in _text(page)
    assert entries["feedback/vault-cleanup"].written_hash == content_hash(page)
    assert "feedback/deduplicate-before-ranking" not in entries


def test_keep_leaves_a_person_s_edit_protected(tmp_vault: Path):
    state = _empty()
    page = _extracted(tmp_vault, state, "feedback", "vault-cleanup",
                      name="Deduplicate before ranking", sources=[S1])
    written = state.entries["feedback/vault-cleanup"].written_hash
    _write(page, _text(page) + "\n(my note)\n")
    _save(tmp_vault, state)

    RA.apply(tmp_vault, Plan(run_id="20260902T152055", llm_calls=0, verdicts=[
        _keep_verdict("vault-cleanup", "shared/feedback/vault-cleanup.md")]),
        rebuild_indexes=False)

    assert "(my note)" in _text(page)
    assert _state(tmp_vault).entries["feedback/vault-cleanup"].written_hash == written


def test_merge_advances_the_target_s_own_key_and_dismisses_the_source_s(tmp_vault: Path):
    state = _empty()
    target = _extracted(tmp_vault, state, "reference", "runtime-heuristic-discovery",
                        name="Use runtime heuristics", sources=[S1])
    dropped = _extracted(tmp_vault, state, "reference", "runtime-heuristic-toggle",
                         name="Toggle runtime heuristics", sources=[S2], absolute=False)
    _save(tmp_vault, state)

    RA.apply(tmp_vault, Plan(run_id="dedupe-audit-20260922-182235", llm_calls=0, verdicts=[
        Verdict(slug="use-runtime-heuristic-toggle", verdict="merge",
                target="use-runtime-heuristic-discovery",
                path="shared/reference/runtime-heuristic-toggle.md",
                target_path="shared/reference/runtime-heuristic-discovery.md")]),
        rebuild_indexes=False)

    entries = _state(tmp_vault).entries
    assert f"  - {S2}\n" in _text(target) and not dropped.exists()
    assert entries["reference/runtime-heuristic-discovery"].written_hash == content_hash(target)
    assert S2 in entries["reference/runtime-heuristic-discovery"].source_files
    assert entries["reference/runtime-heuristic-toggle"].status == "dismissed"
    assert not {"reference/use-runtime-heuristic-discovery",
                "reference/use-runtime-heuristic-toggle"} & set(entries)


def test_reclassify_refuses_a_state_it_cannot_read_before_touching_a_page(tmp_vault: Path):
    state = _empty()
    page = _extracted(tmp_vault, state, "feedback", "vault-cleanup",
                      name="Deduplicate before ranking", sources=[S1])
    state_path = tmp_vault / machine_edits.STATE_REL
    _write(state_path, json.dumps({"schema_version": 99, "entries": {}}))
    before = page.read_bytes()

    with pytest.raises(RuntimeError, match="not readable"):
        RA.apply(tmp_vault, Plan(run_id="r", llm_calls=0, verdicts=[
            _keep_verdict("vault-cleanup", "shared/feedback/vault-cleanup.md")]),
            rebuild_indexes=False)
    assert page.read_bytes() == before
    assert json.loads(_text(state_path))["schema_version"] == 99


# ---------------------------------------------------------------------------
# 2. The drift already left: each known edit, as it composed on the vault
# ---------------------------------------------------------------------------


def test_slug_then_regen_is_explained(tmp_vault: Path):
    """246 demoted pages: rendered absolute, slug-stamped, then regen'd."""
    state = _empty()
    page = _extracted(tmp_vault, state, "reference", "accordion-chevron",
                      name="Accordion chevron", sources=[S1])
    _stamp_slug(page, "accordion-chevron-rotation")
    _old_regen(page, tmp_vault)
    assert _explain(tmp_vault, state, "reference/accordion-chevron", page) == "slug+regen"


@pytest.mark.parametrize("old_link", [
    "briefings/sessions/0f3c9a1e-5b7d-4e2a-9c61-1d2e3f4a5b6c",
    "mnemo://bots/proj/briefings/sessions/0f3c9a1e-5b7d-4e2a-9c61-1d2e3f4a5b6c",
])
def test_an_older_section_spelling_is_rebuilt(tmp_vault: Path, old_link: str):
    state = _empty()
    page = _extracted(tmp_vault, state, "reference", "offline-order",
                      name="Offline order", sources=[S1], absolute=False)
    text = _text(page).replace(f"- [[{S1[:-3]}]]", f"- [[{old_link}]]")
    _write(page, text)
    state.entries["reference/offline-order"].written_hash = content_hash(text)
    _old_regen(page, tmp_vault)
    assert _explain(tmp_vault, state, "reference/offline-order", page) == "regen"


def test_a_section_citing_the_leading_sources_is_rebuilt(tmp_vault: Path):
    """The ledger unioned a source in after the page was rendered."""
    state = _empty()
    page = _extracted(tmp_vault, state, "reference", "mark-form-dirty",
                      name="Mark form dirty", sources=[S1], ledger_sources=[S1, S3])
    text = _text(page).replace(f"  - {_abs(tmp_vault, S1)}\n", f"  - {_abs(tmp_vault, S1)}\n  - {S3}\n")
    _write(page, text)
    state.entries["reference/mark-form-dirty"].written_hash = content_hash(text)
    _old_regen(page, tmp_vault)
    assert _explain(tmp_vault, state, "reference/mark-form-dirty", page) == "regen"


def test_keep_then_slug_then_regen_is_explained(tmp_vault: Path):
    """The 24 kept pages: keep's hash went to the orphan key, so the page's
    own entry still holds the pre-keep hash."""
    state = _empty()
    page = _extracted(tmp_vault, state, "feedback", "vault-cleanup",
                      name="Deduplicate before ranking", sources=[S1])
    _write(page, RA._rewrite_keep(_text(page), "comeca pela higiene entao", f"briefing: {S1}"))
    _stamp_slug(page, "vault-cleanup-before-ranking-work")
    _old_regen(page, tmp_vault)
    assert _explain(tmp_vault, state, "feedback/vault-cleanup", page) == "slug+regen+keep"


def test_one_absolute_source_among_relative_ones_is_explained(tmp_vault: Path):
    """``backup-by-risk-not-ritual``: absolutizing every item overshoots."""
    state = _empty()
    page = _extracted(tmp_vault, state, "reference", "backup-by-risk",
                      name="Backup by risk", sources=[S1, S2, S3], absolute=False)
    text = _text(page).replace(f"  - {S2}\n", f"  - {_abs(tmp_vault, S2)}\n")
    _write(page, text)
    state.entries["reference/backup-by-risk"].written_hash = content_hash(text)
    _write(page, text.replace(f"  - {_abs(tmp_vault, S2)}\n", f"  - {S2}\n"))
    assert _explain(tmp_vault, state, "reference/backup-by-risk", page) == "sources"


def test_a_merge_append_is_explained_from_reclassify_s_archive(tmp_vault: Path):
    """The dedupe audit merged into a slug-stamped, regen'd page; the rebaseline
    reads what it appended off the run's manifest and ``merged/`` copy."""
    state = _empty()
    target = _extracted(tmp_vault, state, "reference", "runtime-heuristic-discovery",
                        name="Use runtime heuristics", sources=[S1])
    _stamp_slug(target, "use-runtime-heuristic-discovery")
    _old_regen(target, tmp_vault)
    _extracted(tmp_vault, state, "reference", "runtime-heuristic-toggle",
               name="Toggle runtime heuristics", sources=[S2, S3], absolute=False)
    drifted_hash = state.entries["reference/runtime-heuristic-discovery"].written_hash
    _save(tmp_vault, state)
    RA.apply(tmp_vault, Plan(run_id="dedupe-audit-20260922-182235", llm_calls=0, verdicts=[
        Verdict(slug="use-runtime-heuristic-toggle", verdict="merge",
                target="use-runtime-heuristic-discovery",
                path="shared/reference/runtime-heuristic-toggle.md",
                target_path="shared/reference/runtime-heuristic-discovery.md")]),
        rebuild_indexes=False)
    state = _state(tmp_vault)
    # The page had already drifted (slug + regen), so the merge left the hash.
    assert state.entries["reference/runtime-heuristic-discovery"].written_hash == drifted_hash

    appends = machine_edits.merge_appends(tmp_vault)
    assert appends["reference/runtime-heuristic-discovery"] == [S2, S3]
    rep = machine_edits.rebaseline(tmp_vault, state)
    assert ("reference/runtime-heuristic-discovery", "slug+regen+merge") in rep.rebaselined
    assert state.entries["reference/runtime-heuristic-discovery"].written_hash == content_hash(target)


# ---------------------------------------------------------------------------
# 3. What a person asked for stays theirs, even on top of machine edits
# ---------------------------------------------------------------------------


def _aliases(t: str) -> str:
    return t.replace("\ntags:\n", "\naliases: [chevron, accordion]\ntags:\n", 1)


def _hand_merge(t: str) -> str:
    return (t.replace(f"{S1}\n", f"{S1}\n  - {S3}\n", 1)
             .replace("Measure before you design.",
                      "Measure before you design.\n\nFolded in from the reference twin."))


def _redaction(t: str) -> str:
    return t.replace("the numbers were wrong", "the token was [redacted]")


def _path_fix(t: str) -> str:
    """The one-off regex of 09-13: a worktree's briefing dir → the project's."""
    return t.replace("bots/proj-wt-187/", "bots/proj/")


def _note_after_section(t: str) -> str:
    """Past regen's marker, but not regen's: a rebuilt section would drop it."""
    return t + "\n\n(user note)\n"


@pytest.mark.parametrize("edit", [_aliases, _hand_merge, _redaction, _path_fix,
                                  _note_after_section],
                         ids=["aliases", "hand-merge", "redaction", "path-fix",
                              "note-after-section"])
def test_a_person_s_requested_edit_on_regen_drift_is_left(tmp_vault: Path, edit):
    state = _empty()
    wt = "bots/proj-wt-187/briefings/sessions/0f3c9a1e-5b7d-4e2a-9c61-1d2e3f4a5b6c.md"
    sources = [wt] if edit is _path_fix else [S1]
    page = _extracted(tmp_vault, state, "reference", "accordion-chevron",
                      name="Accordion chevron", sources=sources)
    _stamp_slug(page, "accordion-chevron-rotation")
    _old_regen(page, tmp_vault)
    # Without the person's edit, the machine drift alone is healed...
    assert _explain(tmp_vault, state, "reference/accordion-chevron", page) == "slug+regen"
    edited = edit(_text(page))
    assert edited != _text(page)
    _write(page, edited)
    written = state.entries["reference/accordion-chevron"].written_hash
    _save(tmp_vault, state)

    # ...with it, nothing is: not by the explainer, not by the rebaseline, and
    # a regen pass after it does not claim the page either.
    assert _explain(tmp_vault, state, "reference/accordion-chevron", page) is None
    rep = machine_edits.rebaseline(tmp_vault, state)
    assert rep.left == ["reference/accordion-chevron"]
    assert state.entries["reference/accordion-chevron"].written_hash == written
    with machine_edits.edit_session(tmp_vault) as session:
        regen._refresh_rule(page, tmp_vault, session)
    assert _state(tmp_vault).entries["reference/accordion-chevron"].written_hash == written


def test_a_note_after_an_extractor_section_is_not_taken_for_regen(tmp_vault: Path):
    """The page regen never touched: its section is the renderer's, and the
    person's note follows it (the extract pipeline's own conflict test)."""
    state = _empty()
    page = _extracted(tmp_vault, state, "feedback", "use-yarn",
                      name="Use yarn", sources=[S1], absolute=False)
    _write(page, _note_after_section(_text(page)))
    assert _explain(tmp_vault, state, "feedback/use-yarn", page) is None


def test_a_windows_absolute_section_is_rebuilt_with_backslashes():
    """A Windows renderer spelled the source, and so the section's link, as
    ``str(Path)``: backslashes throughout."""
    root = "C:\\Users\\me\\vault"
    win = root + "\\" + S1.replace("/", "\\")
    written = (
        "---\nname: x\ndescription: d\ntype: reference\nsources:\n"
        f"  - {win}\n---\n\nBody.\n\n<!-- mnemo:graph-section -->\n## Sources\n- [[{win[:-3]}]]\n"
    )
    regened = regen.refreshed_rule(written, Path(root))
    assert regened != written and f"[[{S1[:-3]}]]" in regened
    assert machine_edits.explain_drift(
        regened, content_hash(written), [root], [S1], (), Path(root)) == "regen"
