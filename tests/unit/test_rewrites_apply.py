"""Applying staged rewrites, and the ledger reconciliation that stops the loop (#159).

Four of five staging sites compare ``content_hash(live)`` against
``entry.written_hash`` and stage a ``.proposed.md`` when they differ. Promotion
was a manual ``mv`` that never advanced ``written_hash``, so every run re-derived
the same rewrite — verified 35/35 drifted on the real vault, ``written_at``
spanning 2026-05 to 2026-09. If ``apply`` does not advance the hash, the merged
result is itself a "user edit" and re-proposes on the next run.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core.extract.inbox.io import content_hash
from mnemo.core.rewrites import apply as A
from mnemo.core.rewrites import classify as C


def _seed(vault: Path, slug: str = "a__x", *, page_type: str = "project") -> None:
    fm = f"name: n\nslug: {slug}\ntype: {page_type}\nsources:\n  - bots/a/memory/{slug}.md"
    live = vault / "shared" / page_type / f"{slug}.md"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text(f"---\n{fm}\n---\n\nline one\n", encoding="utf-8")

    prop = vault / "shared" / "_inbox" / page_type / f"{slug}.proposed.md"
    prop.parent.mkdir(parents=True, exist_ok=True)
    prop.write_text(f"---\n{fm}\n---\n\nline one\nline two\n", encoding="utf-8")

    state_path = vault / ".mnemo" / "extraction-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    # Merge into any existing state rather than replacing it. An earlier version
    # wrote a fresh single-entry dict every call, so seeding two rules left only
    # the second in the ledger and a test asserting on the first died with a
    # KeyError that looked like an apply() bug.
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {"schema_version": 2, "last_run": "2026-09-01T00:00:00", "entries": {}}
    state["entries"][f"{page_type}/{slug}"] = {
        "source_files": [f"bots/a/memory/{slug}.md"],
        "source_hash": "sha256:aaa",
        # Stale on purpose: this mismatch is what stages a rewrite.
        "written_hash": "sha256:stale",
        "written_at": "2026-05-28T00:00:00",
        "status": "direct",
        "last_sync": "2026-05-28T00:00:00",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")


def test_a_concurrent_run_is_refused_rather_than_racing(tmp_vault: Path):
    """Two runs must not both flush the ledger.

    Each ``apply`` reads the whole ledger, mutates a private copy, and flushes a
    full overwrite — so without a lock the second to flush silently discards the
    first's ``written_hash`` updates while both rule files are already written.
    The first run's proposal is deleted by then, so ``classify`` returns nothing
    and the disagreement is permanently undetectable: strictly worse than the
    bug this module fixes, which at least re-proposed visibly. Reproduced 3/3
    with two threads before the lock landed.
    """
    from mnemo.core import locks

    _seed(tmp_vault, "a__x")
    plan = A.plan(tmp_vault, include={"project/a__x"})

    with locks.try_lock(tmp_vault / ".mnemo" / "rewrites.lock") as held:
        assert held
        with pytest.raises(A.VaultBusy):
            A.apply(plan, tmp_vault)

    # Nothing was written while refused: the proposal is still staged.
    assert (tmp_vault / "shared" / "_inbox" / "project" / "a__x.proposed.md").exists()

    # With the lock free, the same plan applies normally.
    assert A.apply(plan, tmp_vault).merged == 1


def test_undo_survives_a_truncated_manifest(tmp_vault: Path):
    """``undo`` is the recovery path, so it must not be what crashes.

    The manifest was written with a plain ``write_text``, so a process dying
    mid-write left it truncated but *existing* — and ``undo``'s only guard is
    ``exists()``, which turned "nothing to restore" into a ``JSONDecodeError``.
    Strictly worse than the no-manifest case the per-rewrite flush replaced.
    """
    _seed(tmp_vault, "a__x")
    plan = A.plan(tmp_vault, include={"project/a__x"})
    report = A.apply(plan, tmp_vault)

    manifest = report.archive_dir / "manifest.json"
    manifest.write_text(manifest.read_text()[: len(manifest.read_text()) // 2])

    assert A.undo(tmp_vault, plan.run_id) == 0


def test_undo_restages_the_consumed_proposal(tmp_vault: Path):
    """An undo must reverse both halves, not just the live file.

    ``undo`` restored the live rule and the ledger but never recreated the
    proposal ``apply`` deleted, so the reviewed text was gone for good —
    recoverable only by re-running extraction and hoping it rebuilt the same
    content from source facts that may since have changed.
    """
    _seed(tmp_vault, "a__x")
    prop = tmp_vault / "shared" / "_inbox" / "project" / "a__x.proposed.md"
    prop_before = prop.read_bytes()
    live = tmp_vault / "shared" / "project" / "a__x.md"
    live_before = live.read_bytes()

    plan = A.plan(tmp_vault, include={"project/a__x"})
    A.apply(plan, tmp_vault)
    assert not prop.exists()

    A.undo(tmp_vault, plan.run_id)

    assert live.read_bytes() == live_before
    assert prop.exists()
    assert prop.read_bytes() == prop_before


def test_a_hard_failure_mid_batch_still_leaves_the_applied_work_recoverable(
    tmp_vault: Path, monkeypatch
):
    """An uncaught I/O error must not strand what already landed.

    The original shape wrote the manifest and flushed the ledger once, after the
    loop. Monkeypatching ``atomic_write`` to fail on the second of three rewrites
    left the first written to disk with its proposal deleted, no manifest at all,
    and a ledger still carrying the stale hash — unrecoverable by ``undo`` and
    silently disagreeing with disk.

    ``_flush`` now runs per rewrite and again in a ``finally``.
    """
    import mnemo.core.rewrites.apply as apply_mod

    for slug in ("a__one", "a__two", "a__three"):
        _seed(tmp_vault, slug)

    real_atomic_write = apply_mod.atomic_write
    calls = {"n": 0}

    def flaky(path: Path, content: str) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk on fire")
        real_atomic_write(path, content)

    monkeypatch.setattr(apply_mod, "atomic_write", flaky)

    plan = A.plan(
        tmp_vault,
        include={"project/a__one", "project/a__two", "project/a__three"},
    )
    with pytest.raises(OSError):
        A.apply(plan, tmp_vault)

    arch = tmp_vault / "shared" / "_archive" / f"rewrites-{plan.run_id}"
    manifest = json.loads((arch / "manifest.json").read_text())

    # Exactly one rewrite completed, and the manifest names it.
    assert len(manifest["moves"]) == 1
    done_key = manifest["moves"][0]["key"]

    # The ledger on disk agrees with the file on disk for that key.
    state = json.loads((tmp_vault / ".mnemo" / "extraction-state.json").read_text())
    done_slug = done_key.split("/", 1)[1]
    done_live = tmp_vault / "shared" / "project" / f"{done_slug}.md"
    assert state["entries"][done_key]["written_hash"] == content_hash(done_live)

    # And undo can put it back.
    monkeypatch.setattr(apply_mod, "atomic_write", real_atomic_write)
    assert A.undo(tmp_vault, plan.run_id) >= 2  # the rule + the state file


def test_one_malformed_live_rule_does_not_abort_the_batch(tmp_vault: Path):
    """A live page with no frontmatter is skipped; the rest of the batch applies.

    ``merge`` raises ``NoLiveFrontmatter`` for such a page. Letting that
    propagate aborted the whole run partway through — after earlier rewrites were
    already written and their proposals deleted, but before the manifest existed,
    which is the one file ``undo`` needs. One malformed rule must not cost the
    other 34 their rollback path.
    """
    _seed(tmp_vault, "a__good")
    _seed(tmp_vault, "a__bad")
    # Strip the frontmatter from one live page, leaving its proposal staged.
    bad_live = tmp_vault / "shared" / "project" / "a__bad.md"
    bad_live.write_text("no frontmatter here\n", encoding="utf-8")

    plan = A.plan(tmp_vault, include={"project/a__good", "project/a__bad"})
    report = A.apply(plan, tmp_vault)

    assert report.merged == 1
    assert len(report.skipped) == 1
    assert report.skipped[0]["key"] == "project/a__bad"
    assert "merge:" in report.skipped[0]["reason"]

    # The good one landed and its ledger advanced.
    good_live = tmp_vault / "shared" / "project" / "a__good.md"
    state = json.loads((tmp_vault / ".mnemo" / "extraction-state.json").read_text())
    assert state["entries"]["project/a__good"]["written_hash"] == content_hash(good_live)

    # The skipped one kept both its files, so it can be fixed and retried.
    assert bad_live.exists()
    assert (tmp_vault / "shared" / "_inbox" / "project" / "a__bad.proposed.md").exists()

    # The manifest exists, so the applied rewrite is still undoable.
    assert (report.archive_dir / "manifest.json").exists()


def test_apply_reconciles_written_hash_so_the_rewrite_stops_regenerating(tmp_vault: Path):
    _seed(tmp_vault)
    plan = A.plan(tmp_vault, include={"project/a__x"})

    report = A.apply(plan, tmp_vault)

    assert report.merged == 1
    live = tmp_vault / "shared" / "project" / "a__x.md"
    state = json.loads((tmp_vault / ".mnemo" / "extraction-state.json").read_text())
    entry = state["entries"]["project/a__x"]
    assert entry["written_hash"] == content_hash(live)
    # The proposal is gone, so a second classify finds nothing to re-propose.
    assert C.classify(tmp_vault) == []


def test_originals_hold_pristine_live_bytes_and_manifest_shape(tmp_vault: Path):
    _seed(tmp_vault)
    before = (tmp_vault / "shared" / "project" / "a__x.md").read_bytes()
    plan = A.plan(tmp_vault, include={"project/a__x"})

    report = A.apply(plan, tmp_vault)

    original = report.archive_dir / "originals" / "a__x.md"
    assert original.read_bytes() == before

    manifest = json.loads((report.archive_dir / "manifest.json").read_text())
    assert manifest["run_id"] == plan.run_id
    assert manifest["state_backup"].endswith("extraction-state.json")
    move = manifest["moves"][0]
    assert move["key"] == "project/a__x"
    assert move["kind"] == "insert_only"
    assert move["action"] == "merge"
    assert move["live_path"] == "shared/project/a__x.md"


def test_undo_restores_bytes_and_state_exactly(tmp_vault: Path):
    _seed(tmp_vault)
    live = tmp_vault / "shared" / "project" / "a__x.md"
    state_path = tmp_vault / ".mnemo" / "extraction-state.json"
    live_before = live.read_bytes()
    state_before = state_path.read_bytes()
    plan = A.plan(tmp_vault, include={"project/a__x"})
    A.apply(plan, tmp_vault)
    assert live.read_bytes() != live_before

    restored = A.undo(tmp_vault, plan.run_id)

    # The rule + the state file + the re-staged proposal. It was 2 before
    # `undo` learned to restore the proposal it had consumed (fe35699); a stale
    # `== 2` here would fail for the right reason and read like an undo bug.
    assert restored == 3
    assert live.read_bytes() == live_before
    assert json.loads(state_path.read_bytes()) == json.loads(state_before)


def test_undo_of_an_unknown_run_restores_nothing(tmp_vault: Path):
    _seed(tmp_vault)

    assert A.undo(tmp_vault, "20260101T000000") == 0


def test_reapplying_the_same_run_id_is_refused(tmp_vault: Path):
    _seed(tmp_vault)
    plan = A.plan(tmp_vault, include={"project/a__x"})
    A.apply(plan, tmp_vault)

    _seed(tmp_vault)  # re-stage so there is something to apply
    with pytest.raises(RuntimeError, match="already applied"):
        A.apply(plan, tmp_vault)


def test_proposal_is_removed_on_success(tmp_vault: Path):
    _seed(tmp_vault)
    prop = tmp_vault / "shared" / "_inbox" / "project" / "a__x.proposed.md"
    plan = A.plan(tmp_vault, include={"project/a__x"})

    A.apply(plan, tmp_vault)

    assert not prop.exists()


def test_full_rewrite_uses_replace_and_is_counted_separately(tmp_vault: Path):
    fm = "name: n\nslug: a__z\ntype: project\nsources:\n  - bots/a/memory/a__z.md"
    live = tmp_vault / "shared" / "project" / "a__z.md"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text(f"---\n{fm}\n---\n\nMARKETPLACE_ENABLED = false\n", encoding="utf-8")
    prop = tmp_vault / "shared" / "_inbox" / "project" / "a__z.proposed.md"
    prop.parent.mkdir(parents=True, exist_ok=True)
    prop.write_text(f"---\n{fm}\n---\n\nMARKETPLACE_ENABLED = true\n", encoding="utf-8")
    state_path = tmp_vault / ".mnemo" / "extraction-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"schema_version": 2, "entries": {}}), encoding="utf-8")

    plan = A.plan(tmp_vault, include={"project/a__z"})
    report = A.apply(plan, tmp_vault)

    assert report.replaced == 1 and report.merged == 0
    assert "MARKETPLACE_ENABLED = true" in live.read_text(encoding="utf-8")
    # No state entry existed, so the missing reconciliation is reported, not silent.
    assert any("hash not reconciled" in n for n in report.notes)


def test_empty_plan_writes_no_archive(tmp_vault: Path):
    _seed(tmp_vault)
    plan = A.plan(tmp_vault, include=set())

    report = A.apply(plan, tmp_vault)

    assert report.merged == 0
    assert report.archive_dir is None
    assert not (tmp_vault / "shared" / "_archive").exists()
