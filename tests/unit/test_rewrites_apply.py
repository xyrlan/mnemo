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
