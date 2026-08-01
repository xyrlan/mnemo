"""The backfill origin stamp must be durable, not re-derived every run.

``_parse_pages_from_response`` computes ``origin_backfill`` from the sources
present in the chunk it is handed. A harvested memory file is dirty exactly
once, so from the second extract onwards it is not in any chunk and the flag
arrives False — even for a slug that is already staged in ``_inbox`` awaiting
review. A later extract that re-emits that slug from a live source then walks
it into ``shared/`` (Task 9b, the eighth bypass).

Two doors, and closing one moves the leak to the other:

* the single-source auto-promote door in ``inbox/paths._target_path_for_page``
  — leaves the ``_inbox`` copy behind, so the vault holds both;
* the universal-promotion door — deletes the ``_inbox`` copy on the way out.

So every test that asserts the page did not reach ``shared/`` also asserts the
staged copy is still there; an assertion on only one of the two passes with
half a fix.
"""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core import llm as llm_mod
from mnemo.core.extract import run_extraction


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _resp(pages):
    return llm_mod.LLMResponse(
        text=json.dumps({"pages": pages}), total_cost_usd=0.0,
        input_tokens=1, output_tokens=1, api_key_source="subscription", raw={},
    )


def _cfg(root: Path) -> dict:
    return {"vaultRoot": str(root), "extraction": {
        "model": "m", "chunkSize": 10, "hintThreshold": 5,
        "preferAPI": False, "subprocessTimeout": 60, "costSoftCap": None}}


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "shared").mkdir(parents=True)
    (root / "mnemo.config.json").write_text(json.dumps({"vaultRoot": str(root)}))
    return root


def _memory(
    root: Path, agent: str, stem: str, *,
    type_: str = "feedback", origin: str | None = None, body: str = "Use pathlib.",
) -> Path:
    """Write a memory file. ``origin`` nests under ``metadata:`` like harvest does."""
    stamp = f"metadata:\n  origin: {origin}\n" if origin else ""
    d = root / "bots" / agent / "memory"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{stem}.md"
    path.write_text(
        f"---\nname: Prefer pathlib\ntype: {type_}\ndescription: d\n{stamp}"
        f"---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _emit(
    monkeypatch, sources: list[str], slug: str = "prefer-pathlib",
    body: str = "Use pathlib.",
):
    monkeypatch.setattr(llm_mod, "call", lambda *a, **k: _resp([{
        "slug": slug, "type": "feedback", "name": "Prefer pathlib",
        "description": "d", "body": body, "source_files": sources,
    }]))


def _state(root: Path) -> dict:
    return json.loads(
        (root / ".mnemo" / "extraction-state.json").read_text(encoding="utf-8")
    )


def _downgrade_state(root: Path) -> None:
    """Strip ``origin_backfill`` from every entry — a pre-Task-9b state file.

    Also what one run of an older mnemo binary does to a current vault: its
    ``atomic_write_state`` builds the payload without the key.
    """
    path = root / ".mnemo" / "extraction-state.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for entry in payload["entries"].values():
        entry.pop("origin_backfill", None)
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert "origin_backfill" not in path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# the bug: a later extract that no longer sees the backfill source
# --------------------------------------------------------------------------

def test_a_later_extract_cannot_launder_a_staged_page(tmp_path, monkeypatch):
    """The whole sequence, at the unit level: stage, then re-emit from a live source.

    Run 2's chunk holds only the live file, so the page arrives with one
    source and no stamp — the exact shape ``_target_path_for_page``
    auto-promotes. Its *merged* sources span two projects, so the universal
    door is open too. Both must stay shut.
    """
    root = _vault(tmp_path)
    _memory(root, "alpha", "prefer-pathlib", origin="backfill")
    _emit(monkeypatch, ["bots/alpha/memory/prefer-pathlib.md"])
    run_extraction(_cfg(root))

    staged = root / "shared" / "_inbox" / "feedback" / "prefer-pathlib.md"
    assert staged.exists(), "precondition: run 1 staged the page"

    # Weeks later: a live memory file in another project, same lesson. A
    # distinct filename keeps it off the harvested file's `<type>/<slug>`
    # state key, which is a separate (unfixed) collision.
    _memory(root, "beta", "always-use-pathlib", origin=None)
    _emit(monkeypatch, ["bots/beta/memory/always-use-pathlib.md"])
    run_extraction(_cfg(root))

    assert not (root / "shared" / "feedback" / "prefer-pathlib.md").exists(), (
        "a page staged for review was laundered into the sacred dir"
    )
    assert staged.exists(), "the staged copy was consumed by the universal door"


def test_the_staged_page_keeps_its_stamp_after_the_later_extract(tmp_path, monkeypatch):
    """Rewriting the staged page must not drop the stamp off the file.

    ``_render_page`` reads the flag off the page, so a fix that only taught the
    *routing* helper about stickiness would rewrite the staged file without its
    ``origin:`` line — and the doctor advisory and the reconciler, which both
    read that file, would then disagree with the router about the same page.
    """
    root = _vault(tmp_path)
    _memory(root, "alpha", "prefer-pathlib", origin="backfill")
    _emit(monkeypatch, ["bots/alpha/memory/prefer-pathlib.md"])
    run_extraction(_cfg(root))

    _memory(root, "beta", "always-use-pathlib", origin=None)
    _emit(monkeypatch, ["bots/beta/memory/always-use-pathlib.md"])
    run_extraction(_cfg(root))

    staged = root / "shared" / "_inbox" / "feedback" / "prefer-pathlib.md"
    assert "\norigin: backfill\n" in staged.read_text(encoding="utf-8")


def test_origin_is_persisted_onto_the_state_entry(tmp_path, monkeypatch):
    """The durable home. Without this the flag lives only inside one run."""
    root = _vault(tmp_path)
    _memory(root, "alpha", "prefer-pathlib", origin="backfill")
    _emit(monkeypatch, ["bots/alpha/memory/prefer-pathlib.md"])
    run_extraction(_cfg(root))

    entry = _state(root)["entries"]["feedback/prefer-pathlib"]
    assert entry["origin_backfill"] is True


def test_a_live_page_is_not_marked_backfill(tmp_path, monkeypatch):
    """Control: the stamp is not simply written onto everything."""
    root = _vault(tmp_path)
    _memory(root, "alpha", "prefer-pathlib", origin=None)
    _emit(monkeypatch, ["bots/alpha/memory/prefer-pathlib.md"])
    run_extraction(_cfg(root))

    entry = _state(root)["entries"]["feedback/prefer-pathlib"]
    assert entry["origin_backfill"] is False
    assert (root / "shared" / "feedback" / "prefer-pathlib.md").exists(), (
        "a live single-source page must still auto-promote"
    )


def test_a_live_page_still_auto_promotes_on_a_later_run(tmp_path, monkeypatch):
    """Control for the sticky read itself, over the same two-run shape.

    Same sequence as the laundering test with the stamp removed: if
    stickiness were keyed off "an entry exists" rather than off the origin,
    this page would be trapped in ``_inbox`` forever.
    """
    root = _vault(tmp_path)
    _memory(root, "alpha", "prefer-pathlib", origin=None)
    _emit(monkeypatch, ["bots/alpha/memory/prefer-pathlib.md"])
    run_extraction(_cfg(root))

    _memory(root, "beta", "always-use-pathlib", origin=None)
    _emit(monkeypatch, ["bots/beta/memory/always-use-pathlib.md"])
    run_extraction(_cfg(root))

    assert (root / "shared" / "feedback" / "prefer-pathlib.md").exists()


# --------------------------------------------------------------------------
# vaults whose state file predates the field
# --------------------------------------------------------------------------

def test_an_old_state_file_is_healed_from_the_staged_page(tmp_path, monkeypatch):
    """Existing vaults have entries with no ``origin_backfill`` key.

    They still have the staged file, and it still carries the stamp, so the
    answer is recoverable — and once recovered it is written back.
    """
    root = _vault(tmp_path)
    _memory(root, "alpha", "prefer-pathlib", origin="backfill")
    _emit(monkeypatch, ["bots/alpha/memory/prefer-pathlib.md"])
    run_extraction(_cfg(root))

    _downgrade_state(root)

    _memory(root, "beta", "always-use-pathlib", origin=None)
    _emit(monkeypatch, ["bots/beta/memory/always-use-pathlib.md"])
    run_extraction(_cfg(root))

    assert not (root / "shared" / "feedback" / "prefer-pathlib.md").exists()
    assert (root / "shared" / "_inbox" / "feedback" / "prefer-pathlib.md").exists()
    assert _state(root)["entries"]["feedback/prefer-pathlib"]["origin_backfill"] is True


def test_state_entry_origin_round_trips_without_a_schema_bump(tmp_path):
    """Additive optional field: same schema version, absent key loads False.

    Bumping ``SCHEMA_VERSION`` would make every older mnemo refuse to load a
    vault a newer one has touched (``load_state`` raises on a *newer* schema),
    which is a hard downgrade break in exchange for a field that degrades to
    False on its own.
    """
    from mnemo.core.extract import inbox
    from mnemo.core.extract.inbox.state_io import SCHEMA_VERSION
    from mnemo.core.extract.scanner import ExtractionState, StateEntry

    state = ExtractionState(last_run="r")
    state.entries["feedback/a"] = StateEntry(
        source_files=["bots/a/memory/x.md"], source_hash="sha256:a",
        written_hash="sha256:b", written_at="r", status="inbox",
        origin_backfill=True,
    )
    path = tmp_path / "state.json"
    inbox.atomic_write_state(state, path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION == 2
    assert payload["entries"]["feedback/a"]["origin_backfill"] is True
    assert inbox.load_state(path).entries["feedback/a"].origin_backfill is True

    # A v2 file written before the field existed.
    del payload["entries"]["feedback/a"]["origin_backfill"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert inbox.load_state(path).entries["feedback/a"].origin_backfill is False


def test_force_must_not_wipe_the_staged_page_a_legacy_vault_heals_from(
    tmp_path, monkeypatch,
):
    """``--force`` clears ``_inbox`` before Phase 2 — including the evidence.

    The ninth bypass, and the only one in this family that needs nothing
    unusual from the user: a vault staged by a pre-Task-9b mnemo has no
    ``origin_backfill`` on its entry, so the staged page is the *only* thing
    that still knows. ``_force_clear_inbox_cluster_dirs`` unlinked it before
    the resolver ever got to look, and one ``mnemo extract --force`` then put
    the reconstruction into ``shared/`` unreviewed.
    """
    root = _vault(tmp_path)
    _memory(root, "alpha", "prefer-pathlib", origin="backfill")
    _emit(monkeypatch, ["bots/alpha/memory/prefer-pathlib.md"])
    run_extraction(_cfg(root))

    staged = root / "shared" / "_inbox" / "feedback" / "prefer-pathlib.md"
    assert staged.exists(), "precondition: run 1 staged the page"
    _downgrade_state(root)

    _memory(root, "beta", "always-use-pathlib", origin=None)
    _emit(monkeypatch, ["bots/beta/memory/always-use-pathlib.md"])
    run_extraction(_cfg(root), force=True)

    assert not (root / "shared" / "feedback" / "prefer-pathlib.md").exists(), (
        "--force wiped the staged page, then laundered it into the sacred dir"
    )
    assert staged.exists()


def test_force_still_wipes_live_staged_pages(tmp_path, monkeypatch):
    """Control: the drift-duplicate wipe still does its job for live pages."""
    root = _vault(tmp_path)
    _memory(root, "alpha", "prefer-pathlib", origin=None)
    _memory(root, "beta", "always-use-pathlib", origin=None)
    _emit(monkeypatch, ["bots/alpha/memory/prefer-pathlib.md",
                        "bots/beta/memory/always-use-pathlib.md"])
    run_extraction(_cfg(root))

    orphan = root / "shared" / "_inbox" / "feedback" / "drifted-slug.md"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text(
        "---\nname: Drifted\ntype: feedback\n---\n\nA prior force run's leftover.\n",
        encoding="utf-8",
    )
    run_extraction(_cfg(root), force=True)

    assert not orphan.exists(), "the live drift-duplicate wipe stopped working"


# --------------------------------------------------------------------------
# stickiness must not leak onto pages that already live in shared/
# --------------------------------------------------------------------------

def test_a_backfill_co_citation_does_not_freeze_a_live_sacred_page(
    tmp_path, monkeypatch,
):
    """Stamping an ``auto_promoted`` entry froze **live** pages.

    The upgrade branch fires when a page that already lives in ``shared/`` is
    re-emitted multi-source; it deliberately leaves the sacred file and the
    entry untouched and writes a ``.proposed.md`` for review. Stamping the
    entry there made the page sticky-backfill forever, so every subsequent
    purely-live update took the ``_inbox`` road and the sacred page was never
    refreshed again. There is no staged page under that key, so the origin
    gate has nothing to protect and nothing to lose by skipping it.
    """
    root = _vault(tmp_path)
    _memory(root, "alpha", "prefer-pathlib", origin=None)
    _emit(monkeypatch, ["bots/alpha/memory/prefer-pathlib.md"])
    run_extraction(_cfg(root))

    sacred = root / "shared" / "feedback" / "prefer-pathlib.md"
    assert sacred.exists(), "precondition: the live page auto-promoted"

    # A reconstruction turns up citing the live page alongside itself.
    _memory(root, "beta", "reconstructed", origin="backfill")
    _emit(monkeypatch, ["bots/alpha/memory/prefer-pathlib.md",
                        "bots/beta/memory/reconstructed.md"])
    run_extraction(_cfg(root))

    assert sacred.exists(), "the upgrade branch must leave the sacred file alone"
    assert _state(root)["entries"]["feedback/prefer-pathlib"]["origin_backfill"] is False

    # Weeks later, a purely live update to the live source.
    _memory(root, "alpha", "prefer-pathlib", origin=None, body="Use pathlib everywhere.")
    _emit(monkeypatch, ["bots/alpha/memory/prefer-pathlib.md"],
          body="Use pathlib everywhere.")
    run_extraction(_cfg(root))

    assert "Use pathlib everywhere." in sacred.read_text(encoding="utf-8"), (
        "a live sacred page stopped being refreshed after one backfill "
        "co-citation stamped its state entry"
    )


# --------------------------------------------------------------------------
# the resolver itself
# --------------------------------------------------------------------------

def _page(**kw):
    from mnemo.core.extract.inbox.types import ExtractedPage
    base = dict(
        slug="s", type="feedback", name="n", description="d", body="b",
        source_files=["bots/a/memory/x.md"], source_hash="sha256:x",
    )
    base.update(kw)
    return ExtractedPage(**base)


def _entry(**kw):
    from mnemo.core.extract.scanner import StateEntry
    base = dict(
        source_files=["bots/a/memory/x.md"], source_hash="sha256:x",
        written_hash="", written_at="", status="inbox",
    )
    base.update(kw)
    return StateEntry(**base)


def test_resolver_restores_the_flag_from_the_entry(tmp_path):
    from mnemo.core.extract.inbox.apply import _resolve_sticky_origin

    page = _page()
    _resolve_sticky_origin(page, _entry(origin_backfill=True), tmp_path)
    assert page.origin_backfill is True


def test_resolver_restores_the_flag_from_the_staged_file(tmp_path):
    from mnemo.core.extract.inbox.apply import _resolve_sticky_origin

    staged = tmp_path / "shared" / "_inbox" / "feedback" / "s.md"
    staged.parent.mkdir(parents=True)
    staged.write_text(
        "---\nname: n\ntype: feedback\norigin: backfill\n---\n\nb\n", encoding="utf-8",
    )
    page = _page()
    _resolve_sticky_origin(page, _entry(), tmp_path)
    assert page.origin_backfill is True


def test_resolver_leaves_a_live_page_alone(tmp_path):
    """Neither an entry nor a staged file that says backfill → no flag."""
    from mnemo.core.extract.inbox.apply import _resolve_sticky_origin

    staged = tmp_path / "shared" / "_inbox" / "feedback" / "s.md"
    staged.parent.mkdir(parents=True)
    staged.write_text("---\nname: n\ntype: feedback\n---\n\nb\n", encoding="utf-8")

    page = _page()
    _resolve_sticky_origin(page, _entry(), tmp_path)
    assert page.origin_backfill is False

    page = _page()
    _resolve_sticky_origin(page, None, tmp_path)
    assert page.origin_backfill is False


def test_resolver_never_clears_a_flag_the_page_already_carries(tmp_path):
    from mnemo.core.extract.inbox.apply import _resolve_sticky_origin

    page = _page(origin_backfill=True)
    _resolve_sticky_origin(page, _entry(origin_backfill=False), tmp_path)
    assert page.origin_backfill is True


def test_is_backfill_markdown_on_a_missing_file(tmp_path):
    from mnemo.core.backfill.origin import is_backfill_markdown

    assert is_backfill_markdown(tmp_path / "nope.md") is False


def test_is_backfill_markdown_accepts_the_nested_spelling(tmp_path):
    """A hand-written or harvest-shaped stamp still counts.

    ``scanner.parse_frontmatter`` is flat, so ``metadata:\\n  origin:`` lifts
    to a top-level ``origin`` — but the predicate accepts both spellings so
    this cannot drift on a parser change.
    """
    from mnemo.core.backfill.origin import is_backfill_markdown

    p = tmp_path / "m.md"
    p.write_text(
        "---\nname: n\nmetadata:\n  origin: backfill\n---\n\nb\n", encoding="utf-8",
    )
    assert is_backfill_markdown(p) is True


# --------------------------------------------------------------------------
# the project-type 1:1 pipeline
# --------------------------------------------------------------------------

def test_a_project_page_stays_staged_after_its_source_loses_the_stamp(
    tmp_path, monkeypatch,
):
    """``promote.py`` derives origin from the source file, which normally keeps
    it — but nothing stops a rewrite (or a user) from stripping it. Once the
    page is staged, the entry remembers.

    Run 2 is a ``--force`` run on purpose. Without ``--force``, an unstamped
    re-emission whose ``shared/project/`` target does not exist is *dismissed*
    rather than written, so the leak is invisible and the test would pass
    against a pipeline with no stickiness at all. ``--force`` is the path that
    actually writes, and it is one flag away for any user.
    """
    root = _vault(tmp_path)
    _memory(root, "alpha", "layout", type_="project", origin="backfill")
    monkeypatch.setattr(llm_mod, "call", lambda *a, **k: _resp([]))
    run_extraction(_cfg(root))

    staged = root / "shared" / "_inbox" / "project" / "alpha__layout.md"
    assert staged.exists(), "precondition: run 1 staged the project page"
    assert _state(root)["entries"]["project/alpha__layout"]["origin_backfill"] is True

    _memory(root, "alpha", "layout", type_="project", origin=None, body="Rewritten.")
    run_extraction(_cfg(root), force=True)

    assert not (root / "shared" / "project" / "alpha__layout.md").exists()
    assert staged.exists()


def test_a_legacy_project_vault_heals_from_the_staged_page(tmp_path, monkeypatch):
    """``promote.py`` needs the same file-level recovery the cluster path has.

    Reading only the source file and the entry leaves a pre-Task-9b vault with
    no way back once the source file has lost its stamp — and ``project`` is
    the type backfill produces most, so it is the highest-volume gap.
    """
    root = _vault(tmp_path)
    _memory(root, "alpha", "layout", type_="project", origin="backfill")
    monkeypatch.setattr(llm_mod, "call", lambda *a, **k: _resp([]))
    run_extraction(_cfg(root))

    staged = root / "shared" / "_inbox" / "project" / "alpha__layout.md"
    assert staged.exists(), "precondition: run 1 staged the project page"
    _downgrade_state(root)

    _memory(root, "alpha", "layout", type_="project", origin=None, body="Rewritten.")
    run_extraction(_cfg(root), force=True)

    assert not (root / "shared" / "project" / "alpha__layout.md").exists()
    assert staged.exists()


def test_a_project_page_survives_losing_both_its_stamps(tmp_path, monkeypatch):
    """Source stamp gone *and* staged stamp hand-edited away — the entry holds.

    The three legs of ``_sticky_backfill`` have to be independently load
    bearing. With only the staged-file leg, editing the ``origin:`` line out of
    the staged page (or deleting it) hands the source file back the last word,
    and it no longer has a stamp either.
    """
    root = _vault(tmp_path)
    _memory(root, "alpha", "layout", type_="project", origin="backfill")
    monkeypatch.setattr(llm_mod, "call", lambda *a, **k: _resp([]))
    run_extraction(_cfg(root))

    staged = root / "shared" / "_inbox" / "project" / "alpha__layout.md"
    text = staged.read_text(encoding="utf-8")
    assert "\norigin: backfill\n" in text, "precondition: the staged page was stamped"
    staged.write_text(text.replace("\norigin: backfill\n", "\n"), encoding="utf-8")

    _memory(root, "alpha", "layout", type_="project", origin=None, body="Rewritten.")
    run_extraction(_cfg(root), force=True)

    assert not (root / "shared" / "project" / "alpha__layout.md").exists()
    assert staged.exists()


def test_a_live_project_page_still_promotes_directly(tmp_path, monkeypatch):
    """Control for the project pipeline, including under ``--force``."""
    root = _vault(tmp_path)
    _memory(root, "alpha", "layout", type_="project", origin=None)
    monkeypatch.setattr(llm_mod, "call", lambda *a, **k: _resp([]))
    run_extraction(_cfg(root))
    _memory(root, "alpha", "layout", type_="project", origin=None, body="Rewritten.")
    run_extraction(_cfg(root), force=True)

    assert (root / "shared" / "project" / "alpha__layout.md").exists()
    assert not (root / "shared" / "_inbox" / "project" / "alpha__layout.md").exists()
    assert _state(root)["entries"]["project/alpha__layout"]["origin_backfill"] is False


# --------------------------------------------------------------------------
# the remaining two gates, reached with the staged file's stamp gone
# --------------------------------------------------------------------------

def test_the_reconciler_blocks_a_staged_page_whose_stamp_was_edited_out(
    tmp_path, monkeypatch,
):
    """The end-of-extract reconciler rebuilds the page from the staged *file*.

    Strip the ``origin:`` line out of that file by hand — a two-second edit in
    any markdown editor — and the file no longer answers. The entry still
    does, and the reconciler's door leads into ``shared/`` with the ``_inbox``
    copy deleted on the way out.
    """
    root = _vault(tmp_path)
    _memory(root, "alpha", "prefer-pathlib", origin="backfill")
    _emit(monkeypatch, ["bots/alpha/memory/prefer-pathlib.md"])
    run_extraction(_cfg(root))

    # A second project's source joins the page, so its merged sources cross
    # universalThreshold and the reconciler will consider it every run.
    _memory(root, "beta", "always-use-pathlib", origin=None)
    _emit(monkeypatch, ["bots/alpha/memory/prefer-pathlib.md",
                        "bots/beta/memory/always-use-pathlib.md"])
    run_extraction(_cfg(root))

    staged = root / "shared" / "_inbox" / "feedback" / "prefer-pathlib.md"
    text = staged.read_text(encoding="utf-8")
    assert "\norigin: backfill\n" in text, "precondition: the file carried the stamp"
    staged.write_text(text.replace("\norigin: backfill\n", "\n"), encoding="utf-8")

    # Nothing is dirty now, so this run is the reconciler and nothing else.
    run_extraction(_cfg(root))

    assert not (root / "shared" / "feedback" / "prefer-pathlib.md").exists(), (
        "the reconciler promoted a staged backfill page whose file-level "
        "stamp had been edited away"
    )
    assert staged.exists()


def test_an_unchanged_re_emission_still_persists_a_recovered_origin(
    tmp_path, monkeypatch,
):
    """The skip path writes the stamp too.

    A source file can go dirty (a whitespace edit) and still produce a
    byte-identical page, which takes the ``unchanged_skipped`` fast path
    *before* any apply branch runs. That path is the one chance a vault with
    an old state file gets to record the origin on that run.

    The emitted slug deliberately differs from the source file's stem: the
    orchestrator's per-file bookkeeping loop overwrites ``source_hash`` on any
    entry whose key collides with a source file's ``<type>/<stem>`` key, which
    would keep the page's hash from ever matching.
    """
    root = _vault(tmp_path)
    src = _memory(root, "alpha", "prefer-pathlib", origin="backfill")
    _emit(monkeypatch, ["bots/alpha/memory/prefer-pathlib.md"], slug="use-pathlib")
    run_extraction(_cfg(root))

    _downgrade_state(root)

    # Dirty the source without changing the page the model emits.
    src.write_text(src.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    summary = run_extraction(_cfg(root))

    assert summary.unchanged_skipped == 1, "precondition: took the skip path"
    assert _state(root)["entries"]["feedback/use-pathlib"]["origin_backfill"] is True
