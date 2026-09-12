"""Frontmatter merge policy for staged rewrites (#159).

Policies are not arbitrary. Measured on the real vault: ``sources[]`` deltas in
the auto-merge set are purely the ``/Users/xyrlan/mnemo/`` absolute-path prefix
(#163, fixed v1.3.3) — but 3 of 11 have live=2 → prop=1, so proposal-wins drops
a source. Live ``description`` values are factually stale ("bloqueia assinante em
dia" vs "RESOLVIDO 2026-08-11"). And ``tdd-red-green-per-feature`` flips
``auto-promoted`` → ``needs-review``, which would re-mark a reviewed rule as a
draft.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.filters import parse_frontmatter
from mnemo.core.rewrites import merge as M


def test_sources_are_normalized_and_unioned(tmp_vault: Path):
    live = (
        "---\nname: n\nslug: s\ntype: project\n"
        "sources:\n  - bots/a/memory/s.md\n  - bots/a/memory/extra.md\n---\n\nbody\n"
    )
    proposal = (
        "---\nname: n\nslug: s\ntype: project\n"
        f"sources:\n  - {tmp_vault}/bots/a/memory/s.md\n---\n\nbody\nmore\n"
    )

    out = M.merge_insert_only(live, proposal, vault_root=tmp_vault)

    assert parse_frontmatter(out)["sources"] == [
        "bots/a/memory/s.md",
        "bots/a/memory/extra.md",
    ]
