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

    state = {
        "schema_version": 2,
        "last_run": "2026-09-01T00:00:00",
        "entries": {
            f"{page_type}/{slug}": {
                "source_files": [f"bots/a/memory/{slug}.md"],
                "source_hash": "sha256:aaa",
                # Stale on purpose: this mismatch is what stages a rewrite.
                "written_hash": "sha256:stale",
                "written_at": "2026-05-28T00:00:00",
                "status": "direct",
                "last_sync": "2026-05-28T00:00:00",
            }
        },
    }
    state_path = vault / ".mnemo" / "extraction-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")


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
