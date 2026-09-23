"""#470: tools that edit a tracked page advance ``written_hash``; the drift
they already left is re-baselined only where a known edit explains it."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from mnemo.autopilot.selffix import doctor_fixer
from mnemo.cli.commands.doctor_checks import rules as doctor_rules
from mnemo.core import locks
from mnemo.core.extract import inbox, machine_edits, promote, reference_gate, scanner
from mnemo.core.extract.inbox.io import content_hash
from mnemo.core.extract.inbox.state_io import atomic_write_state, load_state

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_demotions.py"
_spec = importlib.util.spec_from_file_location("measure_demotions_470", _TOOL)
md = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(md)

RUN_1 = "2026-09-01T10:00:00"
RUN_2 = "2026-09-23T12:17:00"


# ---------------------------------------------------------------------------
# Fixtures in the shapes the real vault holds
# ---------------------------------------------------------------------------


def _memory(vault: Path, stem: str, body: str) -> scanner.MemoryFile:
    path = vault / "bots" / "mnemo" / "memory" / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {stem}\ndescription: what the maintainer measured\n"
        f"metadata:\n  type: project\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return scanner._read_memory_file(path, agent="mnemo")


def _legacy_project_page(vault: Path, state: scanner.ExtractionState,
                         stem: str = "project_hook_storm",
                         body: str = "load 118: helpers fired hooks.") -> tuple:
    """A live project page as a pre-#470 promote wrote it: absolute source."""
    f = _memory(vault, stem, body)
    promote.promote_projects([f], state, vault, run_id=RUN_1)
    key = f"project/mnemo__{f.slug}"
    page = vault / "shared" / "project" / f"mnemo__{f.slug}.md"
    legacy = promote._render_project_page(f, run_id=RUN_1)  # no vault_root: absolute
    assert f"  - {f.path}\n" in legacy
    page.write_text(legacy, encoding="utf-8")
    state.entries[key].written_hash = content_hash(legacy)
    return f, key, page


def _old_fixer(page: Path, vault: Path) -> None:
    """What ``source_path_absolute`` did before #470: swap, and nothing else."""
    text = page.read_text(encoding="utf-8")
    abs_src = str(vault / "bots")
    page.write_text(text.replace(f"  - {abs_src}/", "  - bots/"), encoding="utf-8")


def _staged_demotion(vault: Path, state: scanner.ExtractionState, slug: str) -> Path:
    page = inbox.ExtractedPage(
        slug=slug, type="reference", name=slug.replace("-", " "),
        description="d", body="Run the suite with PYTHONPATH=src.",
        source_files=["bots/mnemo/briefings/sessions/s1.md"],
        source_hash=content_hash(slug), unverified_feedback=True,
    )
    inbox.apply_pages([page], state, vault)
    path = vault / "shared" / "_inbox" / "reference" / f"{slug}.md"
    assert path.is_file() and "demoted_from: feedback" in path.read_text(encoding="utf-8")
    return path


def _save(vault: Path, state: scanner.ExtractionState) -> None:
    atomic_write_state(state, vault / machine_edits.STATE_REL)


def _state(vault: Path) -> scanner.ExtractionState:
    return load_state(vault / machine_edits.STATE_REL)


def _empty() -> scanner.ExtractionState:
    return scanner.ExtractionState(last_run=None, entries={})


# ---------------------------------------------------------------------------
# 1. The cause: promote renders what the fixer wants
# ---------------------------------------------------------------------------


def test_promote_renders_the_vault_relative_source(tmp_vault: Path):
    f = _memory(tmp_vault, "project_x", "body")
    state = _empty()
    promote.promote_projects([f], state, tmp_vault, run_id=RUN_1)
    page = tmp_vault / "shared" / "project" / "mnemo__x.md"

    assert "  - bots/mnemo/memory/project_x.md\n" in page.read_text(encoding="utf-8")
    assert state.entries["project/mnemo__x"].source_files == ["bots/mnemo/memory/project_x.md"]
    # So the fixer has nothing to fix on a page promote just wrote.
    assert doctor_fixer.detect_fixable(vault_root=tmp_vault) == []


# ---------------------------------------------------------------------------
# Writers advance written_hash
# ---------------------------------------------------------------------------


def test_the_fixer_advances_written_hash_and_the_next_update_lands(tmp_vault: Path):
    state = _empty()
    f, key, page = _legacy_project_page(tmp_vault, state)
    _save(tmp_vault, state)

    (w,) = doctor_fixer.detect_fixable(vault_root=tmp_vault)
    assert w.kind == "source_path_absolute"
    doctor_fixer.fix_warning(w, vault_root=tmp_vault)

    state = _state(tmp_vault)
    assert state.entries[key].written_hash == content_hash(page)
    f2 = _memory(tmp_vault, "project_hook_storm", "load 118, and the fix.")
    result = promote.promote_projects([f2], state, tmp_vault, run_id=RUN_2)
    assert result.overwrite_safe == [key] and result.sibling_proposed == []
    assert "and the fix." in page.read_text(encoding="utf-8")


def test_the_fixer_leaves_a_user_edited_page_reading_as_edited(tmp_vault: Path):
    state = _empty()
    _, key, page = _legacy_project_page(tmp_vault, state)
    _save(tmp_vault, state)
    page.write_text(page.read_text(encoding="utf-8") + "\n(my note)\n", encoding="utf-8")
    recorded = state.entries[key].written_hash

    (w,) = doctor_fixer.detect_fixable(vault_root=tmp_vault)
    doctor_fixer.fix_warning(w, vault_root=tmp_vault)

    assert _state(tmp_vault).entries[key].written_hash == recorded
    assert "(my note)" in page.read_text(encoding="utf-8")


def test_the_fixer_keeps_crlf_bytes_it_did_not_mean_to_change(tmp_vault: Path):
    state = _empty()
    _, key, page = _legacy_project_page(tmp_vault, state)
    crlf = page.read_bytes().replace(b"\n", b"\r\n")
    page.write_bytes(crlf)
    state.entries[key].written_hash = content_hash(crlf)
    _save(tmp_vault, state)

    doctor_fixer._fix_source_path_moved(
        page, str(tmp_vault / "bots" / "mnemo" / "memory" / "project_hook_storm.md"),
        "bots/mnemo/memory/project_hook_storm.md",
        machine_edits.EditSession(tmp_vault, _state(tmp_vault)),
    )
    assert page.read_bytes().count(b"\r\n") == crlf.count(b"\r\n")


def test_the_fixer_stands_down_while_an_extraction_holds_the_vault(tmp_vault: Path, capsys):
    state = _empty()
    _, _, page = _legacy_project_page(tmp_vault, state)
    _save(tmp_vault, state)
    before = page.read_bytes()
    warnings = doctor_fixer.detect_fixable(vault_root=tmp_vault)

    with locks.try_lock(tmp_vault / machine_edits.LOCK_REL) as held:
        assert held
        assert doctor_fixer.open_doctor_fix_pr(
            warnings, vault_root=tmp_vault, repo_root=None) is None
        with pytest.raises(machine_edits.VaultBusy):
            doctor_fixer.fix_warning(warnings[0], vault_root=tmp_vault)

    assert page.read_bytes() == before
    assert "doctor fix skipped" in capsys.readouterr().out


def test_the_stamp_advances_written_hash_so_the_page_takes_its_update(tmp_vault: Path):
    state = _empty()
    path = _staged_demotion(tmp_vault, state, "suite-needs-pythonpath")
    _save(tmp_vault, state)
    sample = [{"id": "reference/suite-needs-pythonpath", "text": "", "projects": [], "mtime": 0}]

    todo, _ = md.plan_stamps(tmp_vault, sample,
                             {"reference/suite-needs-pythonpath": "G"}, reference_gate.LABELS)
    assert md.apply_stamps(todo, tmp_vault) == 1

    entry = _state(tmp_vault).entries["reference/suite-needs-pythonpath"]
    assert "reference_gate: generic" in path.read_text(encoding="utf-8")
    assert entry.written_hash == content_hash(path)


# ---------------------------------------------------------------------------
# 2. The guard: which drifts a known edit explains
# ---------------------------------------------------------------------------


def test_explain_drift_names_each_known_edit_and_their_composition(tmp_vault: Path):
    state = _empty()
    _, key, page = _legacy_project_page(tmp_vault, state)
    written = state.entries[key].written_hash
    prefixes = machine_edits.vault_prefixes(tmp_vault)
    original = page.read_text(encoding="utf-8")

    _old_fixer(page, tmp_vault)
    swapped = page.read_text(encoding="utf-8")
    assert machine_edits.explain_drift(swapped, written, prefixes) == "sources"

    stamped = swapped.replace("runtime: false\n", "runtime: false\nreference_gate: generic\n")
    assert machine_edits.explain_drift(stamped, written, prefixes) == "sources+reference_gate"

    unslugged = original.replace("slug: mnemo__hook-storm\n", "")
    # A page written before #114 carried no slug:, and the stamp added one.
    assert machine_edits.explain_drift(
        original, content_hash(unslugged), prefixes) == "slug"


@pytest.mark.parametrize("edit", [
    lambda t: t + "\n(my note)\n",
    lambda t: t.replace("load 118", "load 119"),
    lambda t: t.replace("runtime: false\n", "runtime: false\ntags:\n  - mine\n"),
])
def test_a_person_s_edit_is_never_explained_even_on_top_of_a_known_one(tmp_vault: Path, edit):
    state = _empty()
    _, key, page = _legacy_project_page(tmp_vault, state)
    written = state.entries[key].written_hash
    _old_fixer(page, tmp_vault)
    edited = edit(page.read_text(encoding="utf-8"))
    assert content_hash(edited) != content_hash(page)

    assert machine_edits.explain_drift(
        edited, written, machine_edits.vault_prefixes(tmp_vault)) is None


def test_a_moved_vault_s_prefix_is_read_off_a_page_that_kept_it(tmp_path: Path):
    old_root = "/Users/someone/old-vault"
    written = f"---\nname: x\nsources:\n  - {old_root}/bots/a/memory/x.md\n---\n\nbody\n"
    kept = written.replace("name: x", "name: y")
    swapped = written.replace(f"{old_root}/bots/", "bots/")

    prefixes = machine_edits.vault_prefixes(tmp_path / "vault", [kept])
    assert old_root in prefixes
    assert machine_edits.explain_drift(swapped, content_hash(written), prefixes) == "sources"


def test_a_windows_prefix_is_rejoined_with_backslashes():
    written = "---\nsources:\n  - C:\\Users\\me\\vault\\bots\\a\\memory\\x.md\n---\n\nb\n"
    swapped = "---\nsources:\n  - bots/a/memory/x.md\n---\n\nb\n"
    assert machine_edits.absolutize_sources(swapped, "C:\\Users\\me\\vault") == written


def test_only_the_sources_block_is_absolutized():
    text = "---\ntags:\n  - bots/looks-like-a-path\nsources:\n  - bots/a.md\n---\n\n- bots/body\n"
    out = machine_edits.absolutize_sources(text, "/v")
    assert out == "---\ntags:\n  - bots/looks-like-a-path\nsources:\n  - /v/bots/a.md\n---\n\n- bots/body\n"


# ---------------------------------------------------------------------------
# 4 + 5. The re-baseline, and the siblings the drift diverted
# ---------------------------------------------------------------------------


def _drifted_vault(vault: Path) -> dict:
    """Four drifted pages, one of each population the 09-23 vault had."""
    state = _empty()
    # Sources swap only.
    _, swap_key, swap_page = _legacy_project_page(vault, state,
                                                  "project_swap", "swap body")
    _old_fixer(swap_page, vault)
    # Sources swap, then an update diverted into a sibling by the old promote.
    f, sib_key, sib_page = _legacy_project_page(vault, state, "project_sib", "v1")
    _old_fixer(sib_page, vault)
    f2 = _memory(vault, "project_sib", "v2 — the diverted update")
    state.entries[sib_key].source_hash = "sha256:before-the-sibling-fix"
    sibling = vault / "shared" / "_inbox" / "project" / "mnemo__sib.proposed.md"
    sibling.parent.mkdir(parents=True, exist_ok=True)
    proposal = promote._render_project_page(f2, run_id=RUN_2)
    sibling.write_text(proposal, encoding="utf-8")
    # A person's edit on top of the swap.
    _, user_key, user_page = _legacy_project_page(vault, state, "project_user", "u")
    _old_fixer(user_page, vault)
    user_page.write_text(user_page.read_text(encoding="utf-8") + "\n(mine)\n", encoding="utf-8")
    user_sibling = vault / "shared" / "_inbox" / "project" / "mnemo__user.proposed.md"
    user_sibling.write_text("---\nname: pending\n---\n\nproposal\n", encoding="utf-8")
    # A stamped staged demotion.
    staged = _staged_demotion(vault, state, "staged-demotion")
    raw = staged.read_bytes()
    staged.write_bytes(md._stamped(raw, "generic"))
    _save(vault, state)
    return dict(state=state, swap=(swap_key, swap_page), sib=(sib_key, sib_page),
                sibling=sibling, proposal=proposal, user=(user_key, user_page),
                user_sibling=user_sibling, staged=("reference/staged-demotion", staged))


def test_rebaseline_moves_only_the_hashes_a_known_edit_explains(tmp_vault: Path):
    v = _drifted_vault(tmp_vault)
    state = v["state"]
    user_key, user_page = v["user"]
    user_hash = state.entries[user_key].written_hash

    rep = machine_edits.rebaseline(tmp_vault, state)

    assert sorted(rep.rebaselined) == sorted([
        (v["swap"][0], "sources"), (v["sib"][0], "sources"),
        (v["staged"][0], "reference_gate"),
    ])
    assert rep.left == [user_key]
    assert state.entries[user_key].written_hash == user_hash
    assert "(mine)" in user_page.read_text(encoding="utf-8")
    for key, page in (v["swap"], v["staged"], v["sib"]):
        assert state.entries[key].written_hash == content_hash(page)
    assert "re-baselined (reference_gate 1, sources 2)" in machine_edits.summary_line(rep)


def test_the_diverted_update_is_applied_and_its_sibling_leaves_the_inbox(tmp_vault: Path):
    v = _drifted_vault(tmp_vault)
    rep = machine_edits.rebaseline(tmp_vault, v["state"])

    sib_key, sib_page = v["sib"]
    assert rep.siblings_applied == [sib_key]
    assert sib_page.read_text(encoding="utf-8") == v["proposal"]
    assert not v["sibling"].exists()
    # A person's page keeps its sibling: that one is theirs to review.
    assert v["user_sibling"].exists()

    # The next run brings the page to today's render, in place, no sibling.
    f = _memory(tmp_vault, "project_sib", "v2 — the diverted update")
    result = promote.promote_projects([f], v["state"], tmp_vault, run_id="2026-09-24T00:00:00")
    assert result.overwrite_safe == [sib_key] and result.sibling_proposed == []
    assert "  - bots/mnemo/memory/project_sib.md\n" in sib_page.read_text(encoding="utf-8")


def test_a_staged_page_s_sibling_is_applied_but_not_when_a_live_page_exists(tmp_vault: Path):
    state = _empty()
    staged = _staged_demotion(tmp_vault, state, "a")
    staged.write_bytes(md._stamped(staged.read_bytes(), "generic"))
    sib = staged.with_name("a.proposed.md")
    sib.write_text("---\nname: a\n---\n\nnewer\n", encoding="utf-8")

    other = _staged_demotion(tmp_vault, state, "b")
    other.write_bytes(md._stamped(other.read_bytes(), "system"))
    other_sib = other.with_name("b.proposed.md")
    other_sib.write_text("---\nname: b\n---\n\nupgrade?\n", encoding="utf-8")
    live = tmp_vault / "shared" / "reference" / "b.md"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text("---\nname: b\n---\n\nlive\n", encoding="utf-8")

    rep = machine_edits.rebaseline(tmp_vault, state)

    assert rep.siblings_applied == ["reference/a"]
    assert staged.read_text(encoding="utf-8").endswith("newer\n") and not sib.exists()
    assert other_sib.exists()
    assert state.entries["reference/b"].written_hash == content_hash(other)


def test_rebaseline_is_idempotent_and_dry_run_touches_nothing(tmp_vault: Path):
    v = _drifted_vault(tmp_vault)
    snapshot = {p: p.read_bytes() for p in (tmp_vault / "shared").rglob("*.md")}
    hashes = {k: e.written_hash for k, e in v["state"].entries.items()}

    dry = machine_edits.rebaseline(tmp_vault, v["state"], dry_run=True)
    assert len(dry.rebaselined) == 3 and len(dry.siblings_applied) == 1
    assert {k: e.written_hash for k, e in v["state"].entries.items()} == hashes
    assert {p: p.read_bytes() for p in (tmp_vault / "shared").rglob("*.md")} == snapshot

    machine_edits.rebaseline(tmp_vault, v["state"])
    again = machine_edits.rebaseline(tmp_vault, v["state"])
    assert again.rebaselined == [] and again.siblings_applied == []
    assert again.left == [v["user"][0]]


def test_doctor_reports_what_the_next_extract_would_rebaseline(tmp_vault: Path, capsys):
    _drifted_vault(tmp_vault)
    before = (tmp_vault / machine_edits.STATE_REL).read_bytes()

    assert doctor_rules._doctor_check_written_hash_drift(tmp_vault) is True
    out = capsys.readouterr().out
    assert "3 page(s) re-baselined" in out and "1 drifted page(s) left" in out
    assert (tmp_vault / machine_edits.STATE_REL).read_bytes() == before


def test_doctor_is_quiet_when_nothing_a_tool_did_is_unrecorded(tmp_vault: Path, capsys):
    state = _empty()
    promote.promote_projects([_memory(tmp_vault, "project_x", "b")], state, tmp_vault)
    _save(tmp_vault, state)

    assert doctor_rules._doctor_check_written_hash_drift(tmp_vault) is True
    assert "no tracked page carries an unrecorded tool edit" in capsys.readouterr().out


def test_doctor_registers_the_drift_check():
    from mnemo.cli.commands.doctor import DOCTOR_CHECKS

    assert ("written_hash_drift", doctor_rules._doctor_check_written_hash_drift) in DOCTOR_CHECKS


# ---------------------------------------------------------------------------
# 3. The sibling churn
# ---------------------------------------------------------------------------


def test_promote_records_a_proposed_source_so_it_is_not_re_rendered(tmp_vault: Path):
    state = _empty()
    f = _memory(tmp_vault, "project_x", "v1")
    promote.promote_projects([f], state, tmp_vault, run_id=RUN_1)
    page = tmp_vault / "shared" / "project" / "mnemo__x.md"
    page.write_text(page.read_text(encoding="utf-8") + "\n(mine)\n", encoding="utf-8")

    f2 = _memory(tmp_vault, "project_x", "v2")
    first = promote.promote_projects([f2], state, tmp_vault, run_id=RUN_1)
    sibling = Path(first.sibling_proposed[0][1])
    written = sibling.read_bytes()

    again = promote.promote_projects([f2], state, tmp_vault, run_id=RUN_2)
    assert again.sibling_proposed == [] and again.unchanged_skipped == ["project/mnemo__x"]
    assert sibling.read_bytes() == written

    # A new source change is still proposed, not lost.
    f3 = _memory(tmp_vault, "project_x", "v3")
    third = promote.promote_projects([f3], state, tmp_vault, run_id=RUN_2)
    assert len(third.sibling_proposed) == 1 and "v3" in sibling.read_text(encoding="utf-8")
    assert "(mine)" in page.read_text(encoding="utf-8")


def test_inbox_flow_records_a_proposed_source_so_it_is_not_re_rendered(tmp_vault: Path):
    state = _empty()
    staged = _staged_demotion(tmp_vault, state, "a")
    staged.write_text(staged.read_text(encoding="utf-8") + "\n(mine)\n", encoding="utf-8")
    page = inbox.ExtractedPage(
        slug="a", type="reference", name="a", description="d", body="newer",
        source_files=["bots/mnemo/briefings/sessions/s2.md"],
        source_hash=content_hash("new source"), unverified_feedback=True,
    )

    first = inbox.apply_pages([page], state, tmp_vault, run_id=RUN_1)
    assert len(first.sibling_proposed) == 1
    again = inbox.apply_pages([page], state, tmp_vault, run_id=RUN_2)
    assert again.sibling_proposed == [] and "reference/a" in again.unchanged_skipped


# ---------------------------------------------------------------------------
# Wiring: extraction re-baselines on the state it saves
# ---------------------------------------------------------------------------


def test_extract_rebaselines_and_its_own_state_write_keeps_it(tmp_vault: Path, monkeypatch):
    from mnemo.core import llm as llm_mod
    from mnemo.core.extract import run_extraction

    v = _drifted_vault(tmp_vault)
    text = json.dumps({"pages": []})

    def fake_call(prompt, *, system, model, timeout):
        return llm_mod.LLMResponse(
            text=text, total_cost_usd=0.0, input_tokens=1, output_tokens=1,
            api_key_source="none", raw={"result": text},
        )

    monkeypatch.setattr(llm_mod, "call", fake_call)
    summary = run_extraction({
        "vaultRoot": str(tmp_vault),
        "extraction": {"model": "claude-haiku-4-5", "chunkSize": 10, "hintThreshold": 5,
                       "preferAPI": False, "subprocessTimeout": 60, "costSoftCap": None},
    })

    assert summary.rebaselined == 3 and summary.siblings_applied == 1
    state = _state(tmp_vault)
    swap_key, swap_page = v["swap"]
    assert state.entries[swap_key].written_hash == content_hash(swap_page)
    user_key, _ = v["user"]
    assert state.entries[user_key].written_hash == v["state"].entries[user_key].written_hash
